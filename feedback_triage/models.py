import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReasonCode = Literal["question_not_request", "decision_not_request", "non_actionable_commentary", "low_confidence"]
ObservationType = Literal["bug", "change_request", "feature_request", "question", "decision", "reaction", "commentary"]
Intent = Literal["explicit_change", "explicit_problem", "ambiguous_reaction", "question", "decision", "none"]
Route = Literal["candidate", "manual_review", "clarification_request", "withheld_result"]
EvidenceFrameStatus = Literal["extracted", "failed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceSpan(StrictModel):
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


class RoutedResult(StrictModel):
    route: Route
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{16}$")
    topic_key: str
    type: ObservationType
    intent: Intent
    title: str
    component: str | None
    summary: str
    requested_outcome: str | None
    acceptance_criteria: list[str]
    clarification_question: str | None
    confidence: Literal["high", "medium", "low"]
    evidence: list[EvidenceSpan]
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
