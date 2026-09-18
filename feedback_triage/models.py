import hashlib
import json
import math
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

ReasonCode = Literal["question_not_request", "decision_not_request", "non_actionable_commentary", "low_confidence"]
ObservationType = Literal["bug", "change_request", "feature_request", "question", "decision", "reaction", "commentary"]
Intent = Literal["explicit_change", "explicit_problem", "ambiguous_reaction", "question", "decision", "none"]
Route = Literal["candidate", "manual_review", "clarification_request", "withheld_result"]
EvidenceFrameStatus = Literal["extracted", "failed"]

SOURCE_SHA256_PATTERN = r"^[0-9a-f]{64}$"
CANDIDATE_ID_PATTERN = r"^cand_[0-9a-f]{16}$"


def _to_tuple(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("immutable collection fields require a list or tuple")
    return tuple(value)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class IssuePayload(StrictModel):
    """The exact rendered request sent to the GitHub Issues adapter."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    labels: Annotated[tuple[str, ...], BeforeValidator(_to_tuple)] = ()

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if not self.title.strip() or not self.body.strip():
            raise ValueError("Issue payload title and body must not be blank")
        if any(not label.strip() for label in self.labels):
            raise ValueError("Issue payload labels must not be blank")
        return self


def issue_payload_hash(payload: IssuePayload) -> str:
    encoded = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class EvidenceSpan(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    start_seconds: float
    end_seconds: float
    keyframe_seconds: float | None = None
    client_quote: str | None = None
    visual_observation: str | None = None

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        values = (self.start_seconds, self.end_seconds, self.keyframe_seconds)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("evidence timestamps must be finite")
        if not any(value and value.strip() for value in (self.client_quote, self.visual_observation)):
            raise ValueError("evidence requires a client quote or visual observation")
        return self


class Observation(StrictModel):
    observation_id: str = Field(pattern=r"^obs_[0-9]{3}$")
    topic_key: str
    type: ObservationType
    intent: Intent
    title: str = Field(min_length=1)
    component: str | None
    summary: str = Field(min_length=1)
    requested_outcome: str | None
    acceptance_criteria: list[str]
    clarification_question: str | None
    confidence: Literal["high", "medium", "low"]
    evidence: list[EvidenceSpan] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_required_text(self) -> Self:
        if any(not value.strip() for value in (self.title, self.summary, self.rationale)):
            raise ValueError("required observation text must not be blank")
        return self


class AnalysisResult(StrictModel):
    schema_version: Literal["1.0"]
    video_summary: str = Field(min_length=1)
    observations: list[Observation] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if not self.video_summary.strip():
            raise ValueError("video summary must not be blank")
        return self


class VerifiedAnalysis(StrictModel):
    analysis: AnalysisResult
    processing_pair_count: int = Field(gt=0)
    gemini_usage: dict[str, int] | None = None

    @model_validator(mode="after")
    def validate_usage(self) -> Self:
        if self.gemini_usage is not None and any(
            not key.strip() or value < 0 for key, value in self.gemini_usage.items()
        ):
            raise ValueError("Gemini usage keys must be non-blank and values must be non-negative")
        return self


class RoutedResult(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    route: Route
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{16}$")
    topic_key: str
    type: ObservationType
    intent: Intent
    title: str
    component: str | None
    summary: str
    requested_outcome: str | None
    acceptance_criteria: Annotated[tuple[str, ...], BeforeValidator(_to_tuple)]
    clarification_question: str | None
    confidence: Literal["high", "medium", "low"]
    evidence: Annotated[tuple[EvidenceSpan, ...], BeforeValidator(_to_tuple)]
    evidence_frame_seconds: float
    rationale: str
    approval_eligible: bool
    visually_inferred: bool
    reason_code: ReasonCode | None

    @model_validator(mode="after")
    def validate_route_permissions(self) -> Self:
        if self.approval_eligible != (self.route == "candidate"):
            raise ValueError("only candidate routes may be Approval-eligible")
        if (self.route == "withheld_result") != (self.reason_code is not None):
            raise ValueError("only withheld results require a reason code")
        return self


def routed_result_hash(result: RoutedResult) -> str:
    """Return the canonical hash of one persisted policy result."""

    encoded = json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class PolicyResult(StrictModel):
    schema_version: Literal["1.0"]
    results: list[RoutedResult]


class EvidenceFrameRecord(StrictModel):
    """The persisted result of extracting one Candidate's Evidence Frame."""

    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{16}$")
    timestamp_seconds: float
    status: EvidenceFrameStatus
    path: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_frame_record(self) -> Self:
        if not math.isfinite(self.timestamp_seconds) or self.timestamp_seconds < 0:
            raise ValueError("Evidence Frame timestamp must be finite and non-negative")
        if self.status == "extracted":
            if self.path is None or not self.path.strip():
                raise ValueError("extracted Evidence Frames require a path")
            if self.error is not None:
                raise ValueError("extracted Evidence Frames cannot contain an error")
        elif self.path is not None:
            raise ValueError("failed Evidence Frames cannot contain a path")
        elif self.error is None or not self.error.strip():
            raise ValueError("failed Evidence Frames require an error")
        return self


class Approval(StrictModel):
    """An explicit human decision for one immutable Candidate snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_sha256: str = Field(pattern=SOURCE_SHA256_PATTERN)
    candidate_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    destination_repository: str = Field(pattern=r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
    candidate_snapshot: RoutedResult
    candidate_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: IssuePayload
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_at: str = Field(min_length=1)
    operator_label: str | None = None
    manual_review_confirmed: bool = False

    @model_validator(mode="after")
    def validate_approval(self) -> Self:
        if self.candidate_snapshot.candidate_id != self.candidate_id:
            raise ValueError("Approval Candidate identity does not match its snapshot")
        if self.candidate_snapshot.route == "candidate":
            if not self.candidate_snapshot.approval_eligible:
                raise ValueError("only Approval-eligible Candidates may be approved")
        elif self.candidate_snapshot.route == "manual_review":
            if not self.manual_review_confirmed:
                raise ValueError("Manual Review requires explicit confirmation before Approval")
        else:
            raise ValueError("only policy-admitted Candidates may be approved")
        marker = f"<!-- crework:v1 source_sha256={self.source_sha256} candidate_id={self.candidate_id} -->"
        if self.payload.body.count(marker) != 1 or self.payload.body.count("<!-- crework:v1 ") != 1:
            raise ValueError("Approval payload must contain exactly one stable Candidate marker")
        if issue_payload_hash(self.payload) != self.payload_hash:
            raise ValueError("Approval payload hash does not match its rendered payload")
        try:
            timestamp = datetime.fromisoformat(self.approved_at)
        except ValueError as error:
            raise ValueError("Approval timestamp must be ISO-8601") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Approval timestamp must include a timezone")
        if self.operator_label is not None and not self.operator_label.strip():
            raise ValueError("Approval operator label must not be blank")
        return self


class IssueRecord(StrictModel):
    """A verified, destination-scoped link to one remote GitHub Issue."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_sha256: str = Field(pattern=SOURCE_SHA256_PATTERN)
    candidate_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    destination_repository: str = Field(pattern=r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
    issue_number: int = Field(gt=0)
    html_url: str = Field(min_length=1)
    marker: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        expected = f"<!-- crework:v1 source_sha256={self.source_sha256} candidate_id={self.candidate_id} -->"
        if self.marker != expected:
            raise ValueError("Issue Record marker does not match its source and Candidate identity")
        try:
            timestamp = datetime.fromisoformat(self.recorded_at)
        except ValueError as error:
            raise ValueError("Issue Record timestamp must be ISO-8601") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Issue Record timestamp must include a timezone")
        return self
