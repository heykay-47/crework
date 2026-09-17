import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from feedback_triage.gemini_video import MODEL, PROMPT, GeminiClient, UntrustedInteraction, create_client, run_stored_stream
from feedback_triage.input_video import InvalidInput, VideoInfo, probe_video
from feedback_triage.ledger import RunLedger
from feedback_triage.models import AnalysisResult, VerifiedAnalysis


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


def analysis_fingerprint(source_sha256: str) -> str:
    contract = {
        "source_sha256": source_sha256,
        "model": MODEL,
        "prompt": PROMPT,
        "schema": AnalysisResult.model_json_schema(),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def analyze_recording(video: Path, output: Path, client: GeminiClient | None = None) -> tuple[VideoInfo, VerifiedAnalysis, RunLedger]:
    try:
        source_sha256 = file_sha256(video)
    except OSError as error:
        source_sha256 = unavailable_source_id(video)
        ledger = RunLedger.create(
            output,
            source_sha256=source_sha256,
            fingerprint=analysis_fingerprint(source_sha256),
        )
        attempt_id = ledger.start_attempt()
        ledger.fail(attempt_id, code="invalid_input", detail=str(error))
        raise AnalysisFailed("invalid_input", str(error)) from error
    ledger = RunLedger.create(
        output,
        source_sha256=source_sha256,
        fingerprint=analysis_fingerprint(source_sha256),
    )
    attempt_id = ledger.start_attempt()
    try:
        video_info = probe_video(video)
    except InvalidInput as error:
        ledger.fail(attempt_id, code="invalid_input", detail=str(error))
        raise AnalysisFailed("invalid_input", str(error)) from error
    try:
        if client is None:
            client = create_client()
        verified = run_stored_stream(
            client,
            video,
            on_created=lambda interaction_id: ledger.record_interaction_created(attempt_id, interaction_id),
            on_diagnostic=lambda event_type: ledger.record_diagnostic(attempt_id, event_type),
        )
    except ValidationError as error:
        ledger.fail(attempt_id, code="output_invalid", detail=str(error))
        raise AnalysisFailed("output_invalid", str(error)) from error
    except UntrustedInteraction as error:
        ledger.fail(attempt_id, code=error.code, detail=str(error))
        raise AnalysisFailed(error.code, str(error)) from error
    except Exception as error:
        ledger.fail(attempt_id, code="interaction_unrecoverable", detail=str(error))
        raise AnalysisFailed("interaction_unrecoverable", str(error)) from error
    ledger.complete(attempt_id, verified.analysis.model_dump(mode="json"))
    return video_info, verified, ledger
