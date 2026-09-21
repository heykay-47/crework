"""Local browser adapter for the Feedback Recording review workflow.

The browser is intentionally a thin client.  It receives allowlisted DTOs,
while analysis, policy, Evidence Frame extraction, Approval, reconciliation,
and external writes remain owned by the domain services.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
from urllib.parse import urlsplit
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from feedback_triage.analyze import (
    AnalysisFailed,
    analysis_fingerprint,
    analysis_fingerprint_inputs,
    file_sha256,
    triage_recording,
)
from feedback_triage.approval import EDITABLE_FIELDS, build_approval, normalize_destination
from feedback_triage.github import GitHubApiError, GitHubIssueClient
from feedback_triage.gemini_video import PROMPT
from feedback_triage.input_video import InvalidInput, probe_video
from feedback_triage.ledger import LedgerFingerprintMismatch, LedgerInvalid, LedgerLocked, RunLedger
from feedback_triage.models import (
    EvidenceFrameRecord,
    PolicyResult,
    RoutedResult,
    routed_result_hash,
    seconds_to_timecode,
)
from feedback_triage.persistence import atomic_write_json
from feedback_triage.writes import (
    ExternalWriteFailure,
    IssueGateway,
    ReviewDecision,
    WriteCoordinator,
)


ALLOWED_VIDEO_EXTENSIONS = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})
STAGE_LABELS = {
    "upload": "Upload validated",
    "gemini_processing": "Gemini processing",
    "interaction_verification": "Interaction verification",
    "policy_evaluation": "Policy evaluation",
    "evidence_frame_extraction": "Evidence Frame extraction",
}
STAGE_KEYS = tuple(STAGE_LABELS)
STAGE_STATES = {"pending", "running", "completed", "failed"}
RECORDING_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ROUTE_LABELS = {
    "candidate": "Candidate",
    "clarification_request": "Clarification Request",
    "manual_review": "Manual Review",
    "withheld_result": "Withheld Result",
}
ROUTE_DESCRIPTIONS = {
    "candidate": "Ready for an explicit Approval decision.",
    "clarification_request": "Needs a clearer request before it can become an Issue.",
    "manual_review": "Needs explicit human confirmation before Approval.",
    "withheld_result": "Held back by policy; it cannot be approved.",
}
FAILURE_MESSAGES = {
    "invalid_input": "This file is not a valid Feedback Recording. Choose a supported video with a readable duration.",
    "configuration_missing": "Gemini is not configured for this local app. Add the credential configuration and try again.",
    "github_configuration_missing": "GitHub publishing is not configured for this local app.",
    "output_invalid": "Gemini returned output that could not be verified against the trusted schema.",
    "interaction_timeout": "Gemini did not finish in time. The attempt remains recorded and is not trusted.",
    "interaction_unrecoverable": "The Gemini interaction could not be safely recovered. No result was admitted.",
    "interaction_terminal": "The Gemini interaction reached a terminal failure. No result was admitted.",
    "upload_failed": "Gemini could not accept this recording. No result was admitted.",
    "processing_unverified": "Gemini processing could not be verified. No result was admitted.",
    "policy_failed": "Policy evaluation rejected the analysis. No review or write action is available.",
    "attempt_unreconciled": "A previous attempt needs reconciliation before this recording can be analyzed again.",
    "fingerprint_mismatch": "The stored analysis belongs to different behavior inputs. Reanalysis is required and will append an attempt.",
    "frame_failed": "One or more Evidence Frames could not be extracted; the failed frame state is preserved.",
    "reconciliation_failed": "The existing ledger or remote state could not be reconciled safely.",
    "external_write_failed": "GitHub definitively rejected an Issue write. Review the persisted failure before retrying.",
    "external_write_uncertain": "The GitHub write result is uncertain. Reconcile the exact marker before retrying.",
    "external_write_conflict": "Multiple or conflicting GitHub Issues were found. No new Issue was created.",
    "github_write_failed": "GitHub could not complete the requested operation. No unsafe retry was made.",
    "ledger_locked": "Another local operation owns this recording's ledger. Try again after it finishes.",
    "review_not_allowed": "This routed result cannot receive an Approval action.",
    "manual_review_confirmation_required": "Manual Review requires explicit confirmation or an allowed prose edit.",
}
RETRYABLE_FAILURES = frozenset(
    {
        "configuration_missing",
        "github_configuration_missing",
        "interaction_timeout",
        "upload_failed",
        "frame_failed",
        "external_write_failed",
        "external_write_uncertain",
        "github_write_failed",
        "ledger_locked",
    }
)
PUBLISH_FAILURE_CODES = frozenset(
    {
        "github_configuration_missing",
        "external_write_failed",
        "external_write_uncertain",
        "external_write_conflict",
        "github_write_failed",
        "reconciliation_failed",
    }
)


class WebSettings:
    """Filesystem and local integration settings for the app."""

    def __init__(
        self,
        *,
        input_root: Path = Path("/input"),
        output_root: Path = Path("/output"),
        context_root: Path = Path("/context"),
        github_repository: str | None = None,
        github_token: str | None = None,
        max_upload_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        self.input_root = input_root
        self.output_root = output_root
        self.context_root = context_root
        self.github_repository = github_repository
        self.github_token = github_token
        self.max_upload_bytes = max_upload_bytes

    @classmethod
    def from_environment(cls) -> "WebSettings":
        max_upload = os.environ.get("CREWORK_MAX_UPLOAD_BYTES")
        try:
            max_upload_bytes = int(max_upload) if max_upload else 2 * 1024 * 1024 * 1024
        except ValueError:
            max_upload_bytes = 2 * 1024 * 1024 * 1024
        return cls(
            input_root=Path(os.environ.get("CREWORK_INPUT_ROOT", "/input")),
            output_root=Path(os.environ.get("CREWORK_OUTPUT_ROOT", "/output")),
            context_root=Path(os.environ.get("CREWORK_CONTEXT_ROOT", "/context")),
            github_repository=os.environ.get("GITHUB_REPOSITORY"),
            github_token=os.environ.get("GITHUB_TOKEN"),
            max_upload_bytes=max_upload_bytes,
        )


class WebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureDTO(WebModel):
    code: str
    message: str
    retryable: bool


class StageDTO(WebModel):
    key: str
    label: str
    state: Literal["pending", "running", "completed", "failed"]
    progress: None = None


class EvidenceSpanDTO(WebModel):
    start_seconds: float
    end_seconds: float
    keyframe_seconds: float | None
    start_timecode: str
    end_timecode: str
    keyframe_timecode: str | None
    client_quote: str | None
    visual_observation: str | None


class EvidenceFrameDTO(WebModel):
    candidate_id: str
    timestamp_seconds: float
    timestamp_timecode: str
    status: Literal["extracted", "failed"]
    url: str | None
    error: str | None


class CandidateDTO(WebModel):
    candidate_id: str
    topic_key: str
    route: str
    route_label: str
    route_description: str
    type: str
    intent: str
    confidence: str
    evidence_frame_seconds: float
    visually_inferred: bool
    reason_code: str | None
    title: str
    component: str | None
    summary: str
    requested_outcome: str | None
    acceptance_criteria: tuple[str, ...]
    clarification_question: str | None
    rationale: str
    client_quote: str | None
    visual_observation: str | None
    evidence_spans: tuple[EvidenceSpanDTO, ...]
    evidence_frame: EvidenceFrameDTO | None
    approval_eligible: bool
    editable_fields: tuple[str, ...]
    decision: Literal["unreviewed", "approved", "declined"]
    issue_url: str | None
    write_state: str | None


class RouteGroupDTO(WebModel):
    key: str
    label: str
    description: str
    candidates: tuple[CandidateDTO, ...]


class TrustDTO(WebModel):
    source_sha256: str
    duration_seconds: float
    verified: bool
    processing_pair_count: int | None
    model_usage: dict[str, int]
    attempt_id: str | None
    ledger_location: str


class IssueRecordDTO(WebModel):
    candidate_id: str
    destination_repository: str
    issue_number: int
    html_url: str | None


class RecordingSummaryDTO(WebModel):
    recording_id: str
    filename: str
    media_type: str
    size_bytes: int
    duration_seconds: float
    status: str
    stage: StageDTO
    stages: tuple[StageDTO, ...]
    failure: FailureDTO | None
    candidate_count: int
    updated_at: str


class RecordingDetailDTO(RecordingSummaryDTO):
    video_summary: str | None
    groups: tuple[RouteGroupDTO, ...]
    evidence_frames: tuple[EvidenceFrameDTO, ...]
    trust: TrustDTO | None
    issue_records: tuple[IssueRecordDTO, ...]
    destination_repository: str | None
    media_url: str


class ConfigDTO(WebModel):
    destination_repository: str | None
    github_publish_configured: bool
    gemini_configured: bool
    max_upload_bytes: int


class ReviewRequest(WebModel):
    action: Literal["approve", "decline"]
    changes: dict[str, object] = Field(default_factory=dict)
    manual_review_confirmed: bool = False
    operator_label: str | None = None


class PublishRequest(WebModel):
    candidate_ids: tuple[str, ...] = Field(min_length=1)
    retry_failed: bool = False
    retry_uncertain: bool = False
    confirm_no_issue: bool = False
    canonical_issue_selections: dict[str, int] = Field(default_factory=dict)


class IssuePayloadDTO(WebModel):
    title: str
    body: str


class ApprovalPreviewDTO(WebModel):
    candidate_id: str
    destination_repository: str
    payload: IssuePayloadDTO
    editable_fields: tuple[str, ...]
    manual_review_confirmed: bool


class ActionDTO(WebModel):
    status: str
    message: str
    recording: RecordingDetailDTO


@dataclass
class RecordingState:
    recording_id: str
    filename: str
    media_type: str
    size_bytes: int
    duration_seconds: float
    source_path: Path
    status: str = "uploaded"
    current_stage: str = "upload"
    stage_states: dict[str, str] = field(default_factory=lambda: {key: "pending" for key in STAGE_KEYS})
    failure: FailureDTO | None = None
    updated_at: str = ""
    future: Future[None] | None = None
    reanalysis: bool = False


class WebRuntime:
    def __init__(
        self,
        settings: WebSettings,
        *,
        gemini_client: object | None = None,
        github_gateway: IssueGateway | None = None,
    ) -> None:
        self.settings = settings
        self.gemini_client = gemini_client
        self.github_gateway = github_gateway
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feedback-triage")
        self.recordings: dict[str, RecordingState] = {}
        self.metadata_root = settings.output_root / "web" / "recordings"
        self.upload_root = settings.output_root / "web" / "uploads"
        self._load_metadata()
        self._load_existing_ledgers()

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    def _load_metadata(self) -> None:
        try:
            paths = tuple(self.metadata_root.glob("*.json"))
        except OSError:
            return
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                recording_id = _safe_recording_id(data.get("recording_id"))
                if recording_id is None:
                    continue
                source_path = Path(str(data["source_path"])).resolve()
                if not _source_is_allowed(source_path, self.settings) or not source_path.is_file():
                    continue
                stage_states = _safe_stage_states(data.get("stage_states"))
                raw_current_stage = data.get("current_stage", "upload")
                current_stage = raw_current_stage if isinstance(raw_current_stage, str) and raw_current_stage in STAGE_KEYS else "upload"
                state = RecordingState(
                    recording_id=recording_id,
                    filename=str(data["filename"]),
                    media_type=str(data["media_type"]),
                    size_bytes=int(data["size_bytes"]),
                    duration_seconds=float(data["duration_seconds"]),
                    source_path=source_path,
                    status=str(data.get("status", "uploaded")),
                    current_stage=current_stage,
                    stage_states=stage_states,
                    updated_at=str(data.get("updated_at", "")),
                    reanalysis=bool(data.get("reanalysis", False)),
                )
                state.failure = _failure_from_data(data.get("failure"))
                stored_source_sha256 = data.get("source_sha256")
                if isinstance(stored_source_sha256, str):
                    current_source_sha256 = file_sha256(source_path)
                    if current_source_sha256 != stored_source_sha256:
                        state.status = "failed"
                        state.failure = FailureDTO(
                            code="fingerprint_mismatch",
                            message=FAILURE_MESSAGES["fingerprint_mismatch"],
                            retryable=False,
                        )
                ledger_path = self._ledger_path_for(state)
                if ledger_path.is_file() and (
                    state.failure is None
                    or state.failure.code in PUBLISH_FAILURE_CODES
                ):
                    ledger = RunLedger.load(ledger_path)
                    if not _fingerprint_matches(ledger, _read_project_context(self.settings.context_root)):
                        state.status = "failed"
                        state.failure = FailureDTO(
                            code="fingerprint_mismatch",
                            message=FAILURE_MESSAGES["fingerprint_mismatch"],
                            retryable=False,
                        )
                    elif _latest_policy(ledger) is not None:
                        if state.status != "published":
                            state.status = "ready_for_review"
                        state.stage_states = {key: "completed" for key in STAGE_KEYS}
                        state.current_stage = "evidence_frame_extraction"
                self.recordings[recording_id] = state
            except (KeyError, OSError, TypeError, ValueError, LedgerInvalid):
                continue

    def _load_existing_ledgers(self) -> None:
        """Hydrate completed CLI ledgers whose source is still in ``/input``."""

        try:
            ledger_paths = tuple(
                child / "ledger.json"
                for child in self.settings.output_root.iterdir()
                if child.is_dir() and child.name != "web"
            )
            source_paths = tuple(
                path.resolve()
                for path in self.settings.input_root.rglob("*")
                if path.is_file()
                and path.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS
                and _inside(path.resolve(), self.settings.input_root.resolve())
            )
        except OSError:
            return
        project_context = _read_project_context(self.settings.context_root)
        sources_by_hash: dict[str, Path] = {}
        for source_path in source_paths:
            try:
                sources_by_hash[file_sha256(source_path)] = source_path
            except OSError:
                continue
        for ledger_path in ledger_paths:
            try:
                ledger = RunLedger.load(ledger_path)
                if _latest_policy(ledger) is None:
                    continue
                matched_source = sources_by_hash.get(ledger.source_sha256)
                if matched_source is None:
                    continue
                probe = probe_video(matched_source)
                recording_id = f"ledger-{ledger.source_sha256[:24]}"
                if recording_id in self.recordings:
                    continue
                fingerprint_matches = _fingerprint_matches(ledger, project_context)
                state = RecordingState(
                    recording_id=recording_id,
                    filename=matched_source.name,
                    media_type=mimetypes.guess_type(matched_source.name)[0] or "video/*",
                    size_bytes=matched_source.stat().st_size,
                    duration_seconds=probe.duration_seconds,
                    source_path=matched_source,
                    status="ready_for_review" if fingerprint_matches else "failed",
                    current_stage="evidence_frame_extraction",
                    stage_states={key: "completed" for key in STAGE_KEYS},
                )
                if not fingerprint_matches:
                    state.failure = FailureDTO(
                        code="fingerprint_mismatch",
                        message=FAILURE_MESSAGES["fingerprint_mismatch"],
                        retryable=False,
                    )
                self.recordings[recording_id] = state
                self.persist(state)
            except (LedgerInvalid, OSError, InvalidInput, ValueError):
                continue

    def _ledger_path_for(self, state: RecordingState) -> Path:
        try:
            source = file_sha256(state.source_path)
        except OSError:
            return self.settings.output_root / "missing" / "ledger.json"
        return self.settings.output_root / source / "ledger.json"

    def _metadata(self, state: RecordingState) -> dict[str, object]:
        return {
            "recording_id": state.recording_id,
            "filename": state.filename,
            "media_type": state.media_type,
            "size_bytes": state.size_bytes,
            "duration_seconds": state.duration_seconds,
            "source_path": str(state.source_path),
            "source_sha256": file_sha256(state.source_path),
            "status": state.status,
            "current_stage": state.current_stage,
            "stage_states": state.stage_states,
            "failure": state.failure.model_dump(mode="json") if state.failure is not None else None,
            "updated_at": state.updated_at,
            "reanalysis": state.reanalysis,
        }

    def persist(self, state: RecordingState) -> None:
        from datetime import UTC, datetime

        state.updated_at = datetime.now(UTC).isoformat()
        atomic_write_json(
            self._metadata_path(state),
            self._metadata(state),
            prefix=f"{state.recording_id}-",
        )

    def _metadata_path(self, state: RecordingState) -> Path:
        if _safe_recording_id(state.recording_id) is None:
            raise ValueError("recording identity is invalid")
        root = self.metadata_root.resolve()
        path = (root / f"{state.recording_id}.json").resolve()
        if not _inside(path, root):
            raise ValueError("recording metadata path is outside the metadata root")
        return path

    def state(self, recording_id: str) -> RecordingState:
        with self.lock:
            state = self.recordings.get(recording_id)
            if state is None:
                raise HTTPException(status_code=404, detail={"code": "recording_not_found", "message": "Recording not found."})
            return state

    def add(self, state: RecordingState) -> None:
        with self.lock:
            self.recordings[state.recording_id] = state
            self.persist(state)

    def update_stage(self, recording_id: str, stage: str) -> None:
        if stage == "input_validation":
            stage = "upload"
        if stage not in STAGE_LABELS:
            return
        with self.lock:
            state = self.recordings[recording_id]
            previous = state.current_stage
            if previous in state.stage_states and previous != stage:
                state.stage_states[previous] = "completed"
            state.current_stage = stage
            state.stage_states[stage] = "running"
            self.persist(state)

    def submit(self, recording_id: str, *, reanalysis: bool) -> None:
        with self.lock:
            state = self.recordings[recording_id]
            if state.future is not None and not state.future.done():
                raise HTTPException(status_code=409, detail={"code": "analysis_in_progress", "message": "Analysis is already running."})
            if not state.source_path.is_file():
                raise HTTPException(status_code=409, detail={"code": "invalid_input", "message": FAILURE_MESSAGES["invalid_input"]})
            state.status = "analyzing"
            state.failure = None
            state.reanalysis = reanalysis
            state.stage_states = {key: "pending" for key in STAGE_KEYS}
            state.current_stage = "upload"
            state.stage_states["upload"] = "running"
            self.persist(state)
            state.future = self.executor.submit(self._run, recording_id, reanalysis)

    def _run(self, recording_id: str, reanalysis: bool) -> None:
        state = self.recordings[recording_id]
        try:
            if self.gemini_client is None and not _gemini_configured():
                raise AnalysisFailed("configuration_missing", "Gemini credential configuration is missing")
            project_context = _read_project_context(self.settings.context_root)
            triage_recording(
                state.source_path,
                self.settings.output_root,
                client=cast(Any, self.gemini_client),
                reanalyze=reanalysis,
                prompt=PROMPT,
                project_context=project_context,
                extract_evidence_frames=True,
                reset_on_fingerprint_mismatch=reanalysis,
                on_stage=lambda stage: self.update_stage(recording_id, stage),
            )
            with self.lock:
                state.status = "ready_for_review"
                state.failure = None
                if state.current_stage in state.stage_states:
                    state.stage_states[state.current_stage] = "completed"
                self.persist(state)
        except Exception as error:
            failure = _failure_for(error)
            with self.lock:
                state.status = "failed"
                state.failure = failure
                if state.current_stage in state.stage_states:
                    state.stage_states[state.current_stage] = "failed"
                self.persist(state)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_stage_states(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {key: "pending" for key in STAGE_KEYS}
    return {
        key: candidate if isinstance(candidate := value.get(key), str) and candidate in STAGE_STATES else "pending"
        for key in STAGE_KEYS
    }


def _safe_recording_id(value: object) -> str | None:
    if not isinstance(value, str) or not RECORDING_ID_PATTERN.fullmatch(value):
        return None
    return value


def _safe_external_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    if any(character.isspace() or ord(character) < 32 for character in value):
        return None
    return value


def _source_is_allowed(path: Path, settings: WebSettings) -> bool:
    resolved = path.resolve()
    upload_root = settings.output_root / "web" / "uploads"
    return _inside(resolved, upload_root.resolve()) or _inside(resolved, settings.input_root.resolve())


def _gemini_configured() -> bool:
    return any(
        os.environ.get(name, "").strip()
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_APPLICATION_CREDENTIALS")
    )


def _read_project_context(root: Path) -> str:
    for path in (root / "project-context.md", root / "CONTEXT.md"):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def _fingerprint_matches(ledger: RunLedger, project_context: str) -> bool:
    try:
        expected_inputs = analysis_fingerprint_inputs(
            ledger.source_sha256,
            prompt=PROMPT,
            project_context=project_context,
        )
        expected = analysis_fingerprint(
            ledger.source_sha256,
            prompt=PROMPT,
            project_context=project_context,
        )
        persisted_inputs = ledger.fingerprint_inputs
    except (LedgerInvalid, OSError, ValueError):
        return False
    return ledger.analysis_fingerprint == expected and persisted_inputs == expected_inputs


def _failure_from_data(value: object) -> FailureDTO | None:
    if not isinstance(value, dict):
        return None
    try:
        return FailureDTO.model_validate(value)
    except ValueError:
        return None


def _failure_for(error: BaseException) -> FailureDTO:
    if isinstance(error, AnalysisFailed):
        code = error.code
    elif isinstance(error, InvalidInput):
        code = "invalid_input"
    elif isinstance(error, LedgerFingerprintMismatch):
        code = "fingerprint_mismatch"
    elif isinstance(error, LedgerLocked):
        code = "ledger_locked"
    elif isinstance(error, LedgerInvalid):
        code = "reconciliation_failed"
    elif isinstance(error, ExternalWriteFailure):
        code = error.code
    elif isinstance(error, GitHubApiError):
        code = "github_write_failed"
    else:
        code = "interaction_unrecoverable"
    if code not in FAILURE_MESSAGES:
        code = "interaction_unrecoverable"
    return FailureDTO(code=code, message=FAILURE_MESSAGES[code], retryable=code in RETRYABLE_FAILURES)


def _latest_policy(ledger: RunLedger) -> tuple[dict[str, Any], PolicyResult] | None:
    attempts = ledger.attempts
    if not attempts:
        return None
    attempt = attempts[-1]
    value = attempt.get("policy_result")
    if attempt.get("status") != "verified" or not isinstance(value, dict):
        return None
    try:
        return attempt, PolicyResult.model_validate(value)
    except ValueError:
        return None


def _safe_frame_error(frame: EvidenceFrameRecord) -> str | None:
    return "Evidence Frame extraction failed." if frame.status == "failed" else None


def _frame_dto(recording_id: str, frame: EvidenceFrameRecord) -> EvidenceFrameDTO:
    return EvidenceFrameDTO(
        candidate_id=frame.candidate_id,
        timestamp_seconds=frame.timestamp_seconds,
        timestamp_timecode=seconds_to_timecode(frame.timestamp_seconds),
        status=frame.status,
        url=f"/api/recordings/{recording_id}/frames/{frame.candidate_id}" if frame.status == "extracted" else None,
        error=_safe_frame_error(frame),
    )


def _span_dto(span: Any) -> EvidenceSpanDTO:
    return EvidenceSpanDTO(
        start_seconds=span.start_seconds,
        end_seconds=span.end_seconds,
        keyframe_seconds=span.keyframe_seconds,
        start_timecode=seconds_to_timecode(span.start_seconds),
        end_timecode=seconds_to_timecode(span.end_seconds),
        keyframe_timecode=seconds_to_timecode(span.keyframe_seconds) if span.keyframe_seconds is not None else None,
        client_quote=span.client_quote,
        visual_observation=span.visual_observation,
    )


def _candidate_decision(ledger: RunLedger, candidate: RoutedResult, destination: str | None) -> Literal["unreviewed", "approved", "declined"]:
    if destination is not None and candidate.route in {"candidate", "manual_review"}:
        try:
            approval = ledger.latest_approval_for(candidate_id=candidate.candidate_id, destination_repository=destination)
            if approval is not None and approval.candidate_snapshot_hash == routed_result_hash(candidate):
                return "approved"
            baseline = build_approval(
                ledger.source_sha256,
                candidate,
                destination,
                manual_review_confirmed=candidate.route == "manual_review",
            )
            if ledger.has_decline(
                source_sha256=ledger.source_sha256,
                candidate_id=candidate.candidate_id,
                destination_repository=destination,
                candidate_snapshot_hash=routed_result_hash(candidate),
                payload_hash=baseline.payload_hash,
            ):
                return "declined"
        except (LedgerInvalid, ValueError):
            pass
    return "unreviewed"


def _candidate_dto(
    recording_id: str,
    ledger: RunLedger,
    candidate: RoutedResult,
    frames: Mapping[str, EvidenceFrameRecord],
    destination: str | None,
) -> CandidateDTO:
    quotes = tuple(dict.fromkeys(span.client_quote for span in candidate.evidence if span.client_quote))
    observations = tuple(dict.fromkeys(span.visual_observation for span in candidate.evidence if span.visual_observation))
    write_state: str | None = None
    issue_url: str | None = None
    for event in reversed(ledger.latest_write_attempts()):
        if event.get("candidate_id") == candidate.candidate_id:
            value = event.get("state")
            write_state = value if isinstance(value, str) else None
            break
    if destination is not None:
        for record in ledger.issue_records:
            if record.candidate_id == candidate.candidate_id and record.destination_repository == destination:
                issue_url = _safe_external_url(record.html_url)
                break
    return CandidateDTO(
        candidate_id=candidate.candidate_id,
        topic_key=candidate.topic_key,
        route=candidate.route,
        route_label=ROUTE_LABELS[candidate.route],
        route_description=ROUTE_DESCRIPTIONS[candidate.route],
        type=candidate.type,
        intent=candidate.intent,
        confidence=candidate.confidence,
        evidence_frame_seconds=candidate.evidence_frame_seconds,
        visually_inferred=candidate.visually_inferred,
        reason_code=candidate.reason_code,
        title=candidate.title,
        component=candidate.component,
        summary=candidate.summary,
        requested_outcome=candidate.requested_outcome,
        acceptance_criteria=candidate.acceptance_criteria,
        clarification_question=candidate.clarification_question,
        rationale=candidate.rationale,
        client_quote="\n".join(quotes) or None,
        visual_observation="\n".join(observations) or None,
        evidence_spans=tuple(_span_dto(span) for span in candidate.evidence),
        evidence_frame=_frame_dto(recording_id, frames[candidate.candidate_id]) if candidate.candidate_id in frames else None,
        approval_eligible=candidate.approval_eligible,
        editable_fields=tuple(sorted(EDITABLE_FIELDS)) if candidate.route in {"candidate", "manual_review"} else (),
        decision=_candidate_decision(ledger, candidate, destination),
        issue_url=issue_url,
        write_state=write_state,
    )


def _safe_destination(settings: WebSettings) -> str | None:
    if not settings.github_repository:
        return None
    try:
        return normalize_destination(settings.github_repository)
    except ValueError:
        return None


def _ledger_for_state(runtime: WebRuntime, state: RecordingState) -> RunLedger:
    if state.failure is not None and state.failure.code not in PUBLISH_FAILURE_CODES:
        raise HTTPException(status_code=409, detail={"code": state.failure.code, "message": state.failure.message})
    path = runtime._ledger_path_for(state)
    if not path.is_file():
        raise HTTPException(status_code=409, detail={"code": "ledger_unavailable", "message": "This recording has no completed local Run Ledger yet."})
    try:
        return RunLedger.load(path)
    except LedgerInvalid as error:
        raise HTTPException(status_code=409, detail={"code": "reconciliation_failed", "message": FAILURE_MESSAGES["reconciliation_failed"]}) from error


def _summary(runtime: WebRuntime, state: RecordingState) -> RecordingSummaryDTO:
    candidate_count = 0
    try:
        latest = _latest_policy(_ledger_for_state(runtime, state))
        candidate_count = len(latest[1].results) if latest is not None else 0
    except HTTPException:
        pass
    stage = StageDTO(
        key=state.current_stage,
        label=STAGE_LABELS.get(state.current_stage, "Ready for review"),
        state=cast(Literal["pending", "running", "completed", "failed"], state.stage_states.get(state.current_stage, "pending")),
    )
    stages = tuple(
        StageDTO(
            key=key,
            label=label,
            state=cast(Literal["pending", "running", "completed", "failed"], state.stage_states.get(key, "pending")),
        )
        for key, label in STAGE_LABELS.items()
    )
    return RecordingSummaryDTO(
        recording_id=state.recording_id,
        filename=state.filename,
        media_type=state.media_type,
        size_bytes=state.size_bytes,
        duration_seconds=state.duration_seconds,
        status=state.status,
        stage=stage,
        stages=stages,
        failure=state.failure,
        candidate_count=candidate_count,
        updated_at=state.updated_at,
    )


def _detail(runtime: WebRuntime, state: RecordingState) -> RecordingDetailDTO:
    summary = _summary(runtime, state)
    groups: list[RouteGroupDTO] = []
    frames: tuple[EvidenceFrameRecord, ...] = ()
    trust: TrustDTO | None = None
    issue_records: tuple[IssueRecordDTO, ...] = ()
    video_summary: str | None = None
    ledger: RunLedger | None = None
    latest: tuple[dict[str, Any], PolicyResult] | None = None
    try:
        ledger = _ledger_for_state(runtime, state)
        latest = _latest_policy(ledger)
    except HTTPException:
        pass
    if ledger is not None and latest is not None:
        attempt, policy = latest
        attempt_id = attempt.get("attempt_id")
        if not isinstance(attempt_id, str):
            attempt_id = None
        frames = ledger.evidence_frames_for_attempt(attempt_id) if attempt_id else ()
        frame_map = {frame.candidate_id: frame for frame in frames}
        destination = _safe_destination(runtime.settings)
        candidates = tuple(_candidate_dto(state.recording_id, ledger, candidate, frame_map, destination) for candidate in policy.results)
        for route in ("candidate", "clarification_request", "manual_review", "withheld_result"):
            grouped = tuple(candidate for candidate in candidates if candidate.route == route)
            groups.append(RouteGroupDTO(key=route, label=ROUTE_LABELS[route], description=ROUTE_DESCRIPTIONS[route], candidates=grouped))
        analysis = attempt.get("analysis")
        if isinstance(analysis, dict) and isinstance(analysis.get("video_summary"), str):
            video_summary = analysis["video_summary"]
        usage = attempt.get("gemini_usage", {})
        safe_usage = {key: int(value) for key, value in usage.items() if isinstance(key, str) and isinstance(value, int) and value >= 0} if isinstance(usage, dict) else {}
        trust = TrustDTO(
            source_sha256=ledger.source_sha256,
            duration_seconds=state.duration_seconds,
            verified=attempt.get("status") == "verified",
            processing_pair_count=attempt.get("processing_pair_count") if isinstance(attempt.get("processing_pair_count"), int) else None,
            model_usage=safe_usage,
            attempt_id=attempt_id,
            ledger_location=f"{ledger.source_sha256}/ledger.json",
        )
        issue_records = tuple(
            IssueRecordDTO(
                candidate_id=record.candidate_id,
                destination_repository=record.destination_repository,
                issue_number=record.issue_number,
                html_url=_safe_external_url(record.html_url),
            )
            for record in ledger.issue_records
        )
    return RecordingDetailDTO(
        **summary.model_dump(),
        video_summary=video_summary,
        groups=tuple(groups),
        evidence_frames=tuple(_frame_dto(state.recording_id, frame) for frame in frames),
        trust=trust,
        issue_records=issue_records,
        destination_repository=_safe_destination(runtime.settings),
        media_url=f"/api/recordings/{state.recording_id}/media",
    )


def _runtime_from_request(runtime: WebRuntime = Depends()) -> WebRuntime:
    return runtime


def create_app(
    settings: WebSettings | None = None,
    *,
    gemini_client: object | None = None,
    github_gateway: IssueGateway | None = None,
) -> FastAPI:
    """Create the local app with replaceable analysis and GitHub gateways."""

    runtime = WebRuntime(settings or WebSettings.from_environment(), gemini_client=gemini_client, github_gateway=github_gateway)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(title="Feedback Recording review", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.runtime = runtime
    app.dependency_overrides[_runtime_from_request] = lambda: runtime

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config", response_model=ConfigDTO)
    def config() -> ConfigDTO:
        return ConfigDTO(
            destination_repository=_safe_destination(runtime.settings),
            github_publish_configured=runtime.github_gateway is not None or bool(runtime.settings.github_token and _safe_destination(runtime.settings)),
            gemini_configured=runtime.gemini_client is not None or _gemini_configured(),
            max_upload_bytes=runtime.settings.max_upload_bytes,
        )

    @app.get("/api/recordings", response_model=tuple[RecordingSummaryDTO, ...])
    def list_recordings() -> tuple[RecordingSummaryDTO, ...]:
        with runtime.lock:
            return tuple(_summary(runtime, state) for state in sorted(runtime.recordings.values(), key=lambda value: value.updated_at, reverse=True))

    @app.post("/api/recordings", response_model=RecordingDetailDTO, status_code=201)
    async def upload_recording(file: UploadFile = File(...)) -> RecordingDetailDTO:
        filename = Path(file.filename or "").name
        extension = Path(filename).suffix.lower()
        content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if not filename or extension not in ALLOWED_VIDEO_EXTENSIONS or (not content_type.startswith("video/") and content_type not in {"application/octet-stream", "binary/octet-stream"}):
            raise HTTPException(status_code=415, detail={"code": "invalid_input", "message": FAILURE_MESSAGES["invalid_input"]})
        recording_id = str(uuid4())
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or f"recording{extension}"
        destination = (runtime.upload_root / f"{recording_id}-{safe_name}").resolve()
        if not _inside(destination, runtime.upload_root.resolve()):
            raise HTTPException(status_code=400, detail={"code": "invalid_input", "message": FAILURE_MESSAGES["invalid_input"]})
        total = 0
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > runtime.settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail={"code": "invalid_input", "message": "This Feedback Recording exceeds the local upload size limit."})
                    handle.write(chunk)
            info = probe_video(destination)
        except HTTPException:
            destination.unlink(missing_ok=True)
            raise
        except (InvalidInput, OSError):
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=415, detail={"code": "invalid_input", "message": FAILURE_MESSAGES["invalid_input"]})
        state = RecordingState(
            recording_id=recording_id,
            filename=filename,
            media_type=content_type,
            size_bytes=total,
            duration_seconds=info.duration_seconds,
            source_path=destination,
            current_stage="upload",
            stage_states={key: "pending" for key in STAGE_KEYS},
        )
        runtime.add(state)
        return _detail(runtime, state)

    @app.get("/api/recordings/{recording_id}", response_model=RecordingDetailDTO)
    def get_recording(recording_id: str) -> RecordingDetailDTO:
        state = runtime.state(recording_id)
        return _detail(runtime, state)

    def start_analysis(recording_id: str, reanalysis: bool) -> RecordingDetailDTO:
        state = runtime.state(recording_id)
        runtime.submit(recording_id, reanalysis=reanalysis)
        return _detail(runtime, state)

    @app.post("/api/recordings/{recording_id}/analyze", response_model=RecordingDetailDTO, status_code=202)
    def analyze(recording_id: str) -> RecordingDetailDTO:
        return start_analysis(recording_id, False)

    @app.post("/api/recordings/{recording_id}/reanalyze", response_model=RecordingDetailDTO, status_code=202)
    def reanalyze(recording_id: str) -> RecordingDetailDTO:
        return start_analysis(recording_id, True)

    @app.get("/api/recordings/{recording_id}/media")
    def media(recording_id: str) -> FileResponse:
        state = runtime.state(recording_id)
        path = state.source_path.resolve()
        if not _source_is_allowed(path, runtime.settings) or not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "media_not_found", "message": "Recording media is unavailable."})
        return FileResponse(path, media_type=state.media_type, filename=state.filename)

    @app.get("/api/recordings/{recording_id}/frames/{candidate_id}")
    def frame(recording_id: str, candidate_id: str) -> FileResponse:
        state = runtime.state(recording_id)
        ledger = _ledger_for_state(runtime, state)
        latest = _latest_policy(ledger)
        if latest is None:
            raise HTTPException(status_code=404, detail={"code": "frame_not_found", "message": "Evidence Frame is unavailable."})
        attempt_id = latest[0].get("attempt_id")
        if not isinstance(attempt_id, str):
            raise HTTPException(status_code=404, detail={"code": "frame_not_found", "message": "Evidence Frame is unavailable."})
        frame_record = next((value for value in ledger.evidence_frames_for_attempt(attempt_id) if value.candidate_id == candidate_id), None)
        if frame_record is None or frame_record.status != "extracted" or frame_record.path is None:
            raise HTTPException(status_code=404, detail={"code": "frame_not_found", "message": "Evidence Frame is unavailable."})
        path = Path(frame_record.path).resolve()
        frame_root = (runtime._ledger_path_for(state).parent / "evidence-frames").resolve()
        if not _inside(path, frame_root) or not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "frame_not_found", "message": "Evidence Frame is unavailable."})
        return FileResponse(path, media_type="image/png")

    def candidate_for(runtime_state: RecordingState, candidate_id: str) -> tuple[RunLedger, dict[str, Any], RoutedResult]:
        ledger = _ledger_for_state(runtime, runtime_state)
        latest = _latest_policy(ledger)
        if latest is None:
            raise HTTPException(status_code=409, detail={"code": "review_unavailable", "message": "Analysis is not ready for review."})
        attempt, policy = latest
        for candidate in policy.results:
            if candidate.candidate_id == candidate_id:
                return ledger, attempt, candidate
        raise HTTPException(status_code=404, detail={"code": "candidate_not_found", "message": "Candidate not found."})

    @app.get("/api/recordings/{recording_id}/candidates/{candidate_id}/approval-preview", response_model=ApprovalPreviewDTO)
    def approval_preview(recording_id: str, candidate_id: str) -> ApprovalPreviewDTO:
        state = runtime.state(recording_id)
        _, _, candidate = candidate_for(state, candidate_id)
        destination = _safe_destination(runtime.settings)
        if destination is None or candidate.route not in {"candidate", "manual_review"}:
            raise HTTPException(status_code=409, detail={"code": "review_not_allowed", "message": FAILURE_MESSAGES["review_not_allowed"]})
        if candidate.route == "manual_review":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "manual_review_confirmation_required",
                    "message": FAILURE_MESSAGES["manual_review_confirmation_required"],
                },
            )
        try:
            approval = build_approval(
                file_sha256(state.source_path),
                candidate,
                destination,
                manual_review_confirmed=False,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": "review_not_allowed", "message": FAILURE_MESSAGES["review_not_allowed"]}) from error
        return ApprovalPreviewDTO(
            candidate_id=candidate_id,
            destination_repository=destination,
            payload=IssuePayloadDTO(title=approval.payload.title, body=approval.payload.body),
            editable_fields=tuple(sorted(EDITABLE_FIELDS)),
            manual_review_confirmed=approval.manual_review_confirmed,
        )

    @app.post("/api/recordings/{recording_id}/candidates/{candidate_id}/approval-preview", response_model=ApprovalPreviewDTO)
    def approval_preview_with_changes(recording_id: str, candidate_id: str, request: ReviewRequest) -> ApprovalPreviewDTO:
        state = runtime.state(recording_id)
        _, _, candidate = candidate_for(state, candidate_id)
        destination = _safe_destination(runtime.settings)
        if request.action != "approve" or destination is None or candidate.route not in {"candidate", "manual_review"}:
            raise HTTPException(status_code=409, detail={"code": "review_not_allowed", "message": FAILURE_MESSAGES["review_not_allowed"]})
        if candidate.route == "manual_review" and not (request.manual_review_confirmed or request.changes):
            raise HTTPException(status_code=409, detail={"code": "manual_review_confirmation_required", "message": FAILURE_MESSAGES["manual_review_confirmation_required"]})
        try:
            approval = build_approval(
                file_sha256(state.source_path),
                candidate,
                destination,
                changes=request.changes or None,
                manual_review_confirmed=request.manual_review_confirmed or bool(request.changes),
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": "review_not_allowed", "message": FAILURE_MESSAGES["review_not_allowed"]}) from error
        return ApprovalPreviewDTO(
            candidate_id=candidate_id,
            destination_repository=destination,
            payload=IssuePayloadDTO(title=approval.payload.title, body=approval.payload.body),
            editable_fields=tuple(sorted(EDITABLE_FIELDS)),
            manual_review_confirmed=approval.manual_review_confirmed,
        )

    @app.post("/api/recordings/{recording_id}/candidates/{candidate_id}/review", response_model=ActionDTO)
    def review(recording_id: str, candidate_id: str, request: ReviewRequest) -> ActionDTO:
        state = runtime.state(recording_id)
        ledger, attempt, candidate = candidate_for(state, candidate_id)
        destination = _safe_destination(runtime.settings)
        if destination is None or candidate.route not in {"candidate", "manual_review"}:
            raise HTTPException(status_code=409, detail={"code": "review_not_allowed", "message": FAILURE_MESSAGES["review_not_allowed"]})
        if request.action == "approve" and candidate.route == "manual_review" and not (request.manual_review_confirmed or request.changes):
            raise HTTPException(status_code=409, detail={"code": "manual_review_confirmation_required", "message": FAILURE_MESSAGES["manual_review_confirmation_required"]})
        try:
            source_sha256 = file_sha256(state.source_path)
            coordinator = WriteCoordinator(ledger, runtime.github_gateway or _NoGateway())
            coordinator.review_only(
                (candidate,),
                source_sha256=source_sha256,
                destination_repository=destination,
                attempt_id=str(attempt["attempt_id"]),
                decision_fn=lambda _: ReviewDecision(
                    request.action,
                    request.changes,
                    request.manual_review_confirmed or bool(request.changes),
                ),
                operator_label=request.operator_label,
                require_fresh_review=bool(request.changes),
            )
        except (ValueError, LedgerInvalid, LedgerLocked) as error:
            raise HTTPException(status_code=409, detail={"code": "review_not_allowed", "message": FAILURE_MESSAGES["review_not_allowed"]}) from error
        return ActionDTO(status="reviewed", message="Review decision saved locally.", recording=_detail(runtime, state))

    @app.post("/api/recordings/{recording_id}/publish", response_model=ActionDTO)
    def publish(recording_id: str, request: PublishRequest) -> ActionDTO:
        state = runtime.state(recording_id)
        def record_publish_failure(failure: FailureDTO) -> None:
            with runtime.lock:
                state.status = "ready_for_review"
                state.failure = failure
                runtime.persist(state)

        if runtime.github_gateway is None and not runtime.settings.github_token:
            failure = FailureDTO(code="github_configuration_missing", message=FAILURE_MESSAGES["github_configuration_missing"], retryable=True)
            record_publish_failure(failure)
            raise HTTPException(status_code=409, detail=failure.model_dump(mode="json"))
        ledger, attempt, _ = candidate_for(state, request.candidate_ids[0])
        destination = _safe_destination(runtime.settings)
        if destination is None:
            failure = FailureDTO(code="github_configuration_missing", message=FAILURE_MESSAGES["github_configuration_missing"], retryable=True)
            record_publish_failure(failure)
            raise HTTPException(status_code=409, detail=failure.model_dump(mode="json"))
        try:
            approvals = tuple(
                approval
                for candidate_id in request.candidate_ids
                for approval in (ledger.latest_approval_for(candidate_id=candidate_id, destination_repository=destination),)
                if approval is not None
            )
            if len(approvals) != len(set(request.candidate_ids)):
                raise ValueError("all requested Candidates need persisted Approval")
            gateway: IssueGateway
            if runtime.github_gateway is not None:
                gateway = runtime.github_gateway
            else:
                gateway = GitHubIssueClient(cast(str, runtime.settings.github_token))
            with runtime.lock:
                state.status = "publishing"
                state.failure = None
                runtime.persist(state)
            WriteCoordinator(ledger, gateway).publish(
                approvals,
                attempt_id=str(attempt["attempt_id"]),
                retry_failed=request.retry_failed,
                retry_uncertain=request.retry_uncertain,
                confirm_no_issue=request.confirm_no_issue,
                canonical_issue_selections=request.canonical_issue_selections,
            )
        except ExternalWriteFailure as error:
            failure = _failure_for(error)
            record_publish_failure(failure)
            raise HTTPException(status_code=409, detail=failure.model_dump(mode="json")) from error
        except (GitHubApiError, ValueError, LedgerInvalid, LedgerLocked) as error:
            failure = _failure_for(error)
            record_publish_failure(failure)
            raise HTTPException(status_code=409, detail=failure.model_dump(mode="json")) from error
        except Exception as error:
            failure = _failure_for(error)
            record_publish_failure(failure)
            raise HTTPException(status_code=409, detail=failure.model_dump(mode="json")) from error
        with runtime.lock:
            state.status = "published"
            state.failure = None
            runtime.persist(state)
        return ActionDTO(status="published", message="Approved Issues were reconciled and written.", recording=_detail(runtime, state))

    static_root = Path(__file__).with_name("web_static")
    app.mount("/", StaticFiles(directory=static_root, html=True), name="static")
    return app


class _NoGateway:
    def find_marker(self, destination_repository: str, marker: str) -> tuple[Any, ...]:
        raise GitHubApiError("GitHub publishing is not configured", definitive=False)

    def get_issue(self, destination_repository: str, issue_number: int) -> Any:
        raise GitHubApiError("GitHub publishing is not configured", definitive=False)

    def create_issue(self, destination_repository: str, payload: Any) -> Any:
        raise GitHubApiError("GitHub publishing is not configured", definitive=False)


app = create_app()
