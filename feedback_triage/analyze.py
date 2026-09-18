import hashlib
import subprocess
import time
from pathlib import Path

from pydantic import ValidationError

from feedback_triage.gemini_video import (
    MODEL,
    PROMPT,
    GeminiClient,
    InteractionTimeout,
    UntrustedInteraction,
    create_client,
    retrieve_verified_interaction,
    run_stored_stream,
)
from feedback_triage.fingerprint import (
    build_analysis_fingerprint_inputs,
    fingerprint_inputs_digest,
)
from feedback_triage.evidence import extract_evidence_frame
from feedback_triage.input_video import InvalidInput, VideoInfo, probe_video
from feedback_triage.ledger import LedgerFingerprintMismatch, RunLedger
from feedback_triage.models import AnalysisResult, EvidenceFrameRecord, PolicyResult, VerifiedAnalysis
from feedback_triage.policy import PolicyFailure, route_analysis


class AnalysisFailed(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unavailable_source_id(path: Path) -> str:
    return hashlib.sha256(f"unavailable\0{path.absolute()}".encode()).hexdigest()


def analysis_fingerprint_inputs(
    source_sha256: str,
    *,
    prompt: str = PROMPT,
    project_context: str = "",
    fixture_version: str | None = None,
    ground_truth_sha256: str | None = None,
) -> dict[str, str]:
    return build_analysis_fingerprint_inputs(
        source_sha256,
        model=MODEL,
        prompt=prompt,
        schema=AnalysisResult.model_json_schema(),
        project_context=project_context,
        fixture_version=fixture_version,
        ground_truth_sha256=ground_truth_sha256,
    )


def analysis_fingerprint(
    source_sha256: str,
    *,
    prompt: str = PROMPT,
    project_context: str = "",
    fixture_version: str | None = None,
    ground_truth_sha256: str | None = None,
) -> str:
    return fingerprint_inputs_digest(
        analysis_fingerprint_inputs(
            source_sha256,
            prompt=prompt,
            project_context=project_context,
            fixture_version=fixture_version,
            ground_truth_sha256=ground_truth_sha256,
        )
    )


def _ledger_for(
    output: Path,
    *,
    source_sha256: str,
    prompt: str,
    project_context: str,
    fixture_version: str | None,
    ground_truth_sha256: str | None,
    reset_on_fingerprint_mismatch: bool = False,
) -> RunLedger:
    inputs = analysis_fingerprint_inputs(
        source_sha256,
        prompt=prompt,
        project_context=project_context,
        fixture_version=fixture_version,
        ground_truth_sha256=ground_truth_sha256,
    )
    try:
        fingerprint = fingerprint_inputs_digest(inputs)
        if reset_on_fingerprint_mismatch:
            return RunLedger.reset_for_fingerprint(
                output,
                source_sha256=source_sha256,
                fingerprint=fingerprint,
                fingerprint_inputs=inputs,
            )
        return RunLedger.create(output, source_sha256=source_sha256, fingerprint=fingerprint, fingerprint_inputs=inputs)
    except LedgerFingerprintMismatch as error:
        raise AnalysisFailed("fingerprint_mismatch", str(error)) from error


def _saved_verified(attempt: dict[str, object]) -> VerifiedAnalysis:
    pair_count = attempt["processing_pair_count"]
    if not isinstance(pair_count, int):
        raise ValueError("saved processing pair count is invalid")
    raw_usage = attempt.get("gemini_usage")
    usage: dict[str, int] | None = None
    if isinstance(raw_usage, dict):
        parsed_usage: dict[str, int] = {}
        for key, value in raw_usage.items():
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
                parsed_usage[key] = value
        usage = parsed_usage or None
    return VerifiedAnalysis(
        analysis=AnalysisResult.model_validate(attempt["analysis"]),
        processing_pair_count=pair_count,
        gemini_usage=usage,
    )


def analyze_recording(
    video: Path,
    output: Path,
    client: GeminiClient | None = None,
    *,
    reanalyze: bool = False,
    prompt: str = PROMPT,
    project_context: str = "",
    fixture_version: str | None = None,
    ground_truth_sha256: str | None = None,
    reset_on_fingerprint_mismatch: bool = False,
) -> tuple[VideoInfo, VerifiedAnalysis, RunLedger]:
    try:
        source_sha256 = file_sha256(video)
    except OSError as error:
        source_sha256 = unavailable_source_id(video)
        ledger = _ledger_for(
            output,
            source_sha256=source_sha256,
            prompt=prompt,
            project_context=project_context,
            fixture_version=fixture_version,
            ground_truth_sha256=ground_truth_sha256,
            reset_on_fingerprint_mismatch=reset_on_fingerprint_mismatch,
        )
        attempt_id = ledger.start_attempt()
        ledger.fail(attempt_id, code="invalid_input", detail=str(error))
        raise AnalysisFailed("invalid_input", str(error)) from error
    ledger = _ledger_for(
        output,
        source_sha256=source_sha256,
        prompt=prompt,
        project_context=project_context,
        fixture_version=fixture_version,
        ground_truth_sha256=ground_truth_sha256,
        reset_on_fingerprint_mismatch=reset_on_fingerprint_mismatch,
    )
    try:
        video_info = probe_video(video)
    except InvalidInput as error:
        attempt_id = ledger.start_attempt()
        ledger.fail(attempt_id, code="invalid_input", detail=str(error))
        raise AnalysisFailed("invalid_input", str(error)) from error
    attempts = ledger.attempts
    for attempt in attempts:
        interaction_id = attempt.get("interaction_id")
        if attempt["status"] in {"starting", "streaming"}:
            if not isinstance(interaction_id, str) or not interaction_id:
                raise AnalysisFailed("attempt_unreconciled", "prior attempt has no interaction ID")
            if client is None:
                client = create_client()
            retrieve_started = time.monotonic()
            try:
                verified = retrieve_verified_interaction(client, interaction_id)
            except InteractionTimeout as error:
                raise AnalysisFailed(error.code, str(error)) from error
            except ValidationError as error:
                ledger.fail(str(attempt["attempt_id"]), code="output_invalid", detail=str(error))
                if reanalyze:
                    continue
                raise AnalysisFailed("output_invalid", str(error)) from error
            except UntrustedInteraction as error:
                ledger.fail(str(attempt["attempt_id"]), code=error.code, detail=str(error))
                if reanalyze:
                    continue
                raise AnalysisFailed(error.code, str(error)) from error
            ledger.record_measurement(str(attempt["attempt_id"]), "analysis_seconds", time.monotonic() - retrieve_started)
            if verified.gemini_usage is not None:
                ledger.record_gemini_usage(str(attempt["attempt_id"]), verified.gemini_usage)
            ledger.complete(
                str(attempt["attempt_id"]),
                verified.analysis.model_dump(mode="json"),
                processing_pair_count=verified.processing_pair_count,
            )
    if attempts and not reanalyze:
        last = ledger.attempts[-1]
        if last["status"] == "verified":
            return video_info, _saved_verified(last), ledger
        failure = last.get("failure")
        if isinstance(failure, dict):
            code = failure.get("code")
            detail = failure.get("detail")
            if isinstance(code, str) and isinstance(detail, str):
                raise AnalysisFailed(code, detail)
        raise AnalysisFailed("attempt_unreconciled", "prior attempt is not safe to replace")
    attempt_id = ledger.start_attempt()
    try:
        if client is None:
            client = create_client()
        verified = run_stored_stream(
            client,
            video,
            on_created=lambda interaction_id: ledger.record_interaction_created(attempt_id, interaction_id),
            on_diagnostic=lambda event_type: ledger.record_diagnostic(attempt_id, event_type),
            prompt=prompt,
            project_context=project_context,
            on_timing=lambda name, seconds: ledger.record_measurement(attempt_id, name, seconds),
        )
    except ValidationError as error:
        ledger.fail(attempt_id, code="output_invalid", detail=str(error))
        raise AnalysisFailed("output_invalid", str(error)) from error
    except UntrustedInteraction as error:
        attempt = next(item for item in ledger.attempts if item["attempt_id"] == attempt_id)
        if attempt["interaction_id"] is None:
            ledger.fail(attempt_id, code=error.code, detail=str(error))
        raise AnalysisFailed(error.code, str(error)) from error
    except Exception as error:
        attempt = next(item for item in ledger.attempts if item["attempt_id"] == attempt_id)
        if attempt["interaction_id"] is None:
            ledger.fail(attempt_id, code="interaction_unrecoverable", detail=str(error))
        raise AnalysisFailed("interaction_unrecoverable", str(error)) from error
    if verified.gemini_usage is not None:
        ledger.record_gemini_usage(attempt_id, verified.gemini_usage)
    ledger.complete(
        attempt_id,
        verified.analysis.model_dump(mode="json"),
        processing_pair_count=verified.processing_pair_count,
    )
    return video_info, verified, ledger


def _record_evidence_frames(video: Path, ledger: RunLedger, attempt_id: str, policy: PolicyResult) -> None:
    frame_directory = ledger.path.parent / "evidence-frames" / attempt_id
    started = time.monotonic()
    try:
        for result in policy.results:
            if result.route not in {"candidate", "manual_review"}:
                continue
            output_path = frame_directory / f"{result.candidate_id}.png"
            try:
                frame = extract_evidence_frame(
                    video,
                    candidate_id=result.candidate_id,
                    timestamp_seconds=result.evidence_frame_seconds,
                    output_path=output_path,
                )
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                frame = EvidenceFrameRecord(
                    candidate_id=result.candidate_id,
                    timestamp_seconds=result.evidence_frame_seconds,
                    status="failed",
                    error=f"Evidence Frame extraction failed: {error}",
                )
            ledger.record_evidence_frame(attempt_id, frame)
    finally:
        ledger.record_measurement(attempt_id, "frame_seconds", time.monotonic() - started)


def triage_recording(
    video: Path,
    output: Path,
    client: GeminiClient | None = None,
    *,
    reanalyze: bool = False,
    prompt: str = PROMPT,
    project_context: str = "",
    fixture_version: str | None = None,
    ground_truth_sha256: str | None = None,
    extract_evidence_frames: bool = True,
    reset_on_fingerprint_mismatch: bool = False,
) -> tuple[VideoInfo, VerifiedAnalysis, PolicyResult, RunLedger]:
    video_info, verified, ledger = analyze_recording(
        video,
        output,
        client,
        reanalyze=reanalyze,
        prompt=prompt,
        project_context=project_context,
        fixture_version=fixture_version,
        ground_truth_sha256=ground_truth_sha256,
        reset_on_fingerprint_mismatch=reset_on_fingerprint_mismatch,
    )
    attempt_id = str(ledger.attempts[-1]["attempt_id"])
    try:
        policy = route_analysis(
            verified.analysis,
            duration_seconds=video_info.duration_seconds,
            source_sha256=ledger.source_sha256,
        )
    except PolicyFailure as error:
        ledger.fail(attempt_id, code=error.code, detail=str(error))
        raise AnalysisFailed(error.code, str(error)) from error
    ledger.record_policy_result(attempt_id, policy.model_dump(mode="json"))
    if extract_evidence_frames:
        _record_evidence_frames(video, ledger, attempt_id, policy)
    return video_info, verified, policy, ledger
