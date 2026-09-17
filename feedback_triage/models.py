import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        if self.start_seconds < 0 or self.end_seconds < self.start_seconds:
            raise ValueError("evidence timestamps must form a non-negative ordered span")
        if self.keyframe_seconds is not None and not self.start_seconds <= self.keyframe_seconds <= self.end_seconds:
            raise ValueError("evidence keyframe must fall within the evidence span")
        if not any(value and value.strip() for value in (self.client_quote, self.visual_observation)):
            raise ValueError("evidence requires a client quote or visual observation")
        return self


class Observation(StrictModel):
    observation_id: str = Field(pattern=r"^obs_[0-9]{3}$")
    topic_key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    type: Literal["bug", "change_request", "feature_request", "question", "decision", "reaction", "commentary"]
    intent: Literal["explicit_change", "explicit_problem", "ambiguous_reaction", "question", "decision", "none"]
    title: str = Field(min_length=1)
    component: str | None
    summary: str = Field(min_length=1)
    requested_outcome: str | None
    acceptance_criteria: list[str]
    clarification_question: str | None
    confidence: Literal["high", "medium", "low"]
    evidence: list[EvidenceSpan] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class AnalysisResult(StrictModel):
    schema_version: Literal["1.0"]
    video_summary: str = Field(min_length=1)
    observations: list[Observation] = Field(max_length=50)


class VerifiedAnalysis(StrictModel):
    analysis: AnalysisResult
    processing_pair_count: int = Field(gt=0)
