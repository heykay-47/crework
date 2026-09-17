import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from feedback_triage.models import (
    EvidenceSpan,
    Intent,
    ObservationType,
    PolicyResult,
    ReasonCode,
    Route,
    RoutedResult,
    StrictModel,
)


Presence = Literal["required", "forbidden", "any"]


class TextExpectation(StrictModel):
    presence: Presence = "any"
    required_terms: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_terms(self) -> "TextExpectation":
        if any(not term.strip() for term in [*self.required_terms, *self.forbidden_terms]):
            raise ValueError("ground-truth text terms must not be blank")
        if self.presence == "required" and not self.required_terms:
            raise ValueError("required ground-truth text must include required terms")
        if self.presence == "forbidden" and self.required_terms:
            raise ValueError("forbidden ground-truth text cannot include required terms")
        return self


class GroundTruthWindow(StrictModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "GroundTruthWindow":
        if not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds):
            raise ValueError("ground-truth windows must be finite")
        if self.end_seconds <= self.start_seconds:
            raise ValueError("ground-truth windows must be ordered")
        return self


class RequiredOutcome(StrictModel):
    topic_key: str
    type: ObservationType
    intent: Intent
    route: Route
    reason_code: ReasonCode | None
    approval_eligible: bool
    visually_inferred: bool
    candidate_id: str | None = Field(default=None, pattern=r"^cand_[0-9a-f]{16}$")
    requested_outcome: TextExpectation = Field(default_factory=TextExpectation)
    acceptance_criteria: TextExpectation = Field(default_factory=TextExpectation)
    clarification_question: TextExpectation = Field(default_factory=TextExpectation)


class ForbiddenOutcome(StrictModel):
    routes: list[Route] = Field(default_factory=list)


class AllowedWithheldResult(StrictModel):
    topic_key: str = Field(min_length=1)
    authored_window: GroundTruthWindow
    reason_code: ReasonCode


class GroundTruthCase(StrictModel):
    case_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    authored_window: GroundTruthWindow
    required: RequiredOutcome
    forbidden: ForbiddenOutcome
    evidence_frame_required: bool
    client_quote: TextExpectation = Field(default_factory=TextExpectation)
    visual_observation: TextExpectation = Field(default_factory=TextExpectation)


class DuplicateRelationship(StrictModel):
    case_ids: list[str] = Field(min_length=2)
    topic_key: str
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{16}$")


class GroundTruthManifest(StrictModel):
    fixture_version: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(gt=0)
    cases: list[GroundTruthCase] = Field(min_length=1)
    duplicate_relationships: list[DuplicateRelationship] = Field(default_factory=list)
    allowed_withheld_results: list[AllowedWithheldResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_case_ids(self) -> "GroundTruthManifest":
        if not math.isfinite(self.duration_seconds):
            raise ValueError("ground-truth duration must be finite")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("ground-truth case IDs must be unique")
        known_ids = set(case_ids)
        for relationship in self.duplicate_relationships:
            if not set(relationship.case_ids) <= known_ids:
                raise ValueError("duplicate relationship references an unknown case")
        expected_topics = {case.required.topic_key for case in self.cases}
        extra_topics = [extra.topic_key for extra in self.allowed_withheld_results]
        if len(extra_topics) != len(set(extra_topics)):
            raise ValueError("allowed withheld-result topics must be unique")
        if expected_topics.intersection(extra_topics):
            raise ValueError("allowed withheld-result topics must be unexpected topics")
        return self


class ScoreReport(StrictModel):
    passed: bool
    fixture_version: str
    matched_case_ids: list[str]
    actionable_result_count: int = Field(ge=0)
    errors: list[str]


def load_ground_truth(path: Path) -> GroundTruthManifest:
    return GroundTruthManifest.model_validate_json(path.read_text())


def _midpoint(start_seconds: float, end_seconds: float) -> float:
    return (start_seconds + end_seconds) / 2


def _inside(value: float, window: GroundTruthWindow) -> bool:
    return window.start_seconds <= value <= window.end_seconds


def _actual_by_topic(results: list[RoutedResult]) -> dict[str, list[RoutedResult]]:
    grouped: dict[str, list[RoutedResult]] = {}
    for result in results:
        grouped.setdefault(result.topic_key, []).append(result)
    return grouped


def _compare_text(
    label: str,
    expectation: TextExpectation,
    values: list[str],
    case_id: str,
    errors: list[str],
) -> None:
    actual_values = [value.strip() for value in values if value.strip()]
    actual_text = "\n".join(actual_values).casefold()
    if expectation.presence == "required" and not actual_values:
        errors.append(f"{case_id}: required {label} is missing")
    if expectation.presence == "forbidden" and actual_values:
        errors.append(f"{case_id}: unsupported {label} was invented")
    for term in expectation.required_terms:
        if term.casefold() not in actual_text:
            errors.append(f"{case_id}: required {label} term {term!r} is missing")
    for term in expectation.forbidden_terms:
        if term.casefold() in actual_text:
            errors.append(f"{case_id}: forbidden {label} term {term!r} was invented")


def _compare_required(case: GroundTruthCase, result: RoutedResult, errors: list[str]) -> None:
    expected = case.required
    values = (
        ("type", result.type, expected.type),
        ("intent", result.intent, expected.intent),
        ("route", result.route, expected.route),
        ("reason_code", result.reason_code, expected.reason_code),
        ("approval_eligible", result.approval_eligible, expected.approval_eligible),
        ("visually_inferred", result.visually_inferred, expected.visually_inferred),
    )
    for field_name, actual, required in values:
        if actual != required:
            errors.append(f"{case.case_id}: {field_name} is {actual!r}, expected {required!r}")
    if expected.candidate_id is not None and result.candidate_id != expected.candidate_id:
        errors.append(f"{case.case_id}: candidate identity drifted")
    _compare_text(
        "requested outcome",
        expected.requested_outcome,
        [result.requested_outcome] if result.requested_outcome is not None else [],
        case.case_id,
        errors,
    )
    _compare_text("acceptance criteria", expected.acceptance_criteria, result.acceptance_criteria, case.case_id, errors)
    _compare_text(
        "clarification question",
        expected.clarification_question,
        [result.clarification_question] if result.clarification_question is not None else [],
        case.case_id,
        errors,
    )
    if result.route in case.forbidden.routes:
        errors.append(f"{case.case_id}: forbidden route {result.route}")


def _compare_evidence(case: GroundTruthCase, spans: list[EvidenceSpan], errors: list[str]) -> None:
    _compare_text(
        "client evidence",
        case.client_quote,
        [span.client_quote for span in spans if span.client_quote is not None],
        case.case_id,
        errors,
    )
    _compare_text(
        "visual evidence",
        case.visual_observation,
        [span.visual_observation for span in spans if span.visual_observation is not None],
        case.case_id,
        errors,
    )


def score_policy_result(
    policy_result: PolicyResult,
    ground_truth: GroundTruthManifest,
    *,
    source_sha256: str | None = None,
    duration_seconds: float | None = None,
) -> ScoreReport:
    errors: list[str] = []
    if source_sha256 is not None and source_sha256 != ground_truth.source_sha256:
        errors.append("source identity does not match the frozen ground truth")
    if duration_seconds is not None and duration_seconds != ground_truth.duration_seconds:
        errors.append("Feedback Recording duration does not match the frozen ground truth")

    grouped = _actual_by_topic(policy_result.results)
    expected_topics = {case.required.topic_key for case in ground_truth.cases}
    windows_by_topic: dict[str, list[GroundTruthWindow]] = {}
    for case in ground_truth.cases:
        windows_by_topic.setdefault(case.required.topic_key, []).append(case.authored_window)
    allowed_extras = {extra.topic_key: extra for extra in ground_truth.allowed_withheld_results}
    matched_case_ids: list[str] = []

    for case in ground_truth.cases:
        candidates = grouped.get(case.required.topic_key, [])
        if not candidates:
            errors.append(f"{case.case_id}: missing expected topic {case.required.topic_key}")
            continue
        if len(candidates) != 1:
            errors.append(f"{case.case_id}: topic {case.required.topic_key} was not deduplicated")
            continue
        result = candidates[0]
        matched_case_ids.append(case.case_id)
        _compare_required(case, result, errors)
        case_spans = [
            span
            for span in result.evidence
            if _inside(_midpoint(span.start_seconds, span.end_seconds), case.authored_window)
        ]
        _compare_evidence(case, case_spans, errors)
        if not any(_inside(_midpoint(span.start_seconds, span.end_seconds), case.authored_window) for span in result.evidence):
            errors.append(f"{case.case_id}: no Evidence Span midpoint falls inside its authored window")
        if case.evidence_frame_required and not _inside(result.evidence_frame_seconds, case.authored_window):
            errors.append(f"{case.case_id}: required Evidence Frame is outside its authored window")

    for relationship in ground_truth.duplicate_relationships:
        results = grouped.get(relationship.topic_key, [])
        if not results or len(results) != 1:
            errors.append(f"duplicate relationship for {relationship.topic_key} was not represented by one result")
        else:
            expected_case_topics = {
                next(case.required.topic_key for case in ground_truth.cases if case.case_id == case_id)
                for case_id in relationship.case_ids
            }
            if expected_case_topics != {relationship.topic_key}:
                errors.append(f"duplicate relationship for {relationship.topic_key} has inconsistent manifest topics")
            if results[0].candidate_id != relationship.candidate_id:
                errors.append(f"duplicate relationship for {relationship.topic_key} has identity drift")

    for result in policy_result.results:
        if result.topic_key not in expected_topics:
            extra = allowed_extras.get(result.topic_key)
            if result.route != "withheld_result":
                errors.append(f"unexpected actionable result for topic {result.topic_key}")
            elif extra is None:
                errors.append(f"unexpected Withheld Result for topic {result.topic_key}")
            else:
                if result.reason_code != extra.reason_code:
                    errors.append(f"unexpected Withheld Result for topic {result.topic_key} has the wrong reason")
                windows_by_topic[result.topic_key] = [extra.authored_window]
        windows = windows_by_topic.get(result.topic_key, [])
        for span in result.evidence:
            midpoint = _midpoint(span.start_seconds, span.end_seconds)
            if not any(_inside(midpoint, window) for window in windows):
                errors.append(f"{result.topic_key}: Evidence Span midpoint is outside its authored window")

    return ScoreReport(
        passed=not errors,
        fixture_version=ground_truth.fixture_version,
        matched_case_ids=matched_case_ids,
        actionable_result_count=sum(result.route != "withheld_result" for result in policy_result.results),
        errors=errors,
    )
