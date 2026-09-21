import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from feedback_triage.models import AnalysisResult, EvidenceSpan, Observation, PolicyResult, ReasonCode, RoutedResult


TOPIC_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}
Route = Literal["candidate", "manual_review", "clarification_request", "withheld_result"]


class PolicyFailure(ValueError):
    code = "policy_failed"


def candidate_id_for(source_sha256: str, topic_key: str) -> str:
    value = f"v1\0{source_sha256}\0{topic_key}".encode()
    return f"cand_{hashlib.sha256(value).hexdigest()[:16]}"


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _normalize_evidence(span: EvidenceSpan, duration_seconds: float, observation_id: str) -> EvidenceSpan:
    if span.start_seconds < 0 or span.end_seconds < span.start_seconds:
        raise PolicyFailure(
            f"{observation_id}: evidence span {span.start_seconds}-{span.end_seconds}s "
            "must be non-negative and ordered"
        )
    if span.start_seconds > duration_seconds or span.end_seconds > duration_seconds + 0.5:
        raise PolicyFailure(
            f"{observation_id}: evidence span {span.start_seconds}-{span.end_seconds}s exceeds "
            f"the Feedback Recording duration of {duration_seconds}s"
        )
    normalized_end = min(span.end_seconds, duration_seconds)
    if span.keyframe_seconds is not None and not (
        span.start_seconds <= span.keyframe_seconds <= normalized_end
        and span.keyframe_seconds <= duration_seconds
    ):
        raise PolicyFailure(f"{observation_id}: evidence keyframe must fall within the normalized evidence span")
    return span.model_copy(update={"end_seconds": normalized_end})


def _fail(observation: Observation, detail: str) -> PolicyFailure:
    """Point a failed run at the one Observation and field that broke policy."""
    return PolicyFailure(f"{observation.observation_id}: {detail}")


def _validate_observation(observation: Observation, duration_seconds: float) -> list[EvidenceSpan]:
    if not TOPIC_KEY.fullmatch(observation.topic_key):
        raise _fail(observation, f"invalid topic key: {observation.topic_key}")

    requested = _has_text(observation.requested_outcome)
    clarification = _has_text(observation.clarification_question)
    has_quote = any(_has_text(span.client_quote) for span in observation.evidence)
    has_visual = any(_has_text(span.visual_observation) for span in observation.evidence)

    if observation.intent == "explicit_change":
        if observation.type not in {"change_request", "feature_request"}:
            raise _fail(observation, f"explicit_change requires type change_request or feature_request, not {observation.type}")
        if not requested:
            raise _fail(observation, "explicit_change requires a requested outcome")
        if not has_quote:
            raise _fail(observation, "explicit_change requires client-stated evidence")
    if observation.intent == "explicit_problem":
        if observation.type != "bug":
            raise _fail(observation, f"explicit_problem requires type bug, not {observation.type}")
        if not requested:
            raise _fail(observation, "explicit_problem requires an expected outcome")
    if observation.intent == "ambiguous_reaction":
        if observation.type != "reaction":
            raise _fail(observation, f"ambiguous_reaction requires type reaction, not {observation.type}")
        if observation.requested_outcome is not None:
            raise _fail(observation, "ambiguous_reaction requires a null requested_outcome")
        if not clarification:
            raise _fail(observation, "ambiguous_reaction requires a clarification question")
    if (observation.intent == "question") != (observation.type == "question"):
        raise _fail(observation, f"question intent and type must match, got intent {observation.intent} with type {observation.type}")
    if (observation.intent == "decision") != (observation.type == "decision"):
        raise _fail(observation, f"decision intent and type must match, got intent {observation.intent} with type {observation.type}")
    if observation.intent == "none" and not (
        observation.type == "commentary" or (observation.type == "bug" and has_visual)
    ):
        raise _fail(observation, "none intent requires commentary or a visually observed possible bug")
    if observation.type in {"question", "decision", "commentary", "reaction"} and observation.acceptance_criteria:
        raise _fail(observation, "non-actionable observations require empty acceptance criteria")

    return [_normalize_evidence(span, duration_seconds, observation.observation_id) for span in observation.evidence]


def select_evidence_frame_timestamp(evidence: Sequence[EvidenceSpan]) -> float:
    """Choose the deterministic timestamp a reviewer should see first.

    The input is expected to contain already-normalized Evidence Spans. Visual
    observations and supplied keyframes carry more signal than an interval
    midpoint, but the earliest timestamp within each priority class wins.
    """
    if not evidence:
        raise ValueError("at least one Evidence Span is required")

    visual_keyframes = [
        span.keyframe_seconds
        for span in evidence
        if _has_text(span.visual_observation) and span.keyframe_seconds is not None
    ]
    if visual_keyframes:
        return min(visual_keyframes)

    visual_midpoints = [
        (span.start_seconds + span.end_seconds) / 2
        for span in evidence
        if _has_text(span.visual_observation)
    ]
    if visual_midpoints:
        return min(visual_midpoints)

    keyframes = [span.keyframe_seconds for span in evidence if span.keyframe_seconds is not None]
    if keyframes:
        return min(keyframes)

    return min((span.start_seconds + span.end_seconds) / 2 for span in evidence)


def _route_group(
    observations: list[Observation],
    evidence: list[EvidenceSpan],
    source_sha256: str,
) -> RoutedResult:
    canonical = observations[0]
    confidence = max((item.confidence for item in observations), key=CONFIDENCE_ORDER.__getitem__)
    visually_inferred = canonical.type == "bug" and canonical.intent == "none" and any(
        _has_text(span.visual_observation) for span in evidence
    )

    route: Route
    reason_code: ReasonCode | None
    if canonical.type == "question":
        route, reason_code = "withheld_result", "question_not_request"
    elif canonical.type == "decision":
        route, reason_code = "withheld_result", "decision_not_request"
    elif canonical.type == "commentary":
        route, reason_code = "withheld_result", "non_actionable_commentary"
    elif confidence == "low":
        route, reason_code = "withheld_result", "low_confidence"
    elif canonical.intent == "ambiguous_reaction":
        route, reason_code = "clarification_request", None
    elif visually_inferred or confidence == "medium":
        route, reason_code = "manual_review", None
    else:
        route, reason_code = "candidate", None

    acceptance_criteria = list(dict.fromkeys(item for observation in observations for item in observation.acceptance_criteria))
    clarification_question = next(
        (item.clarification_question for item in observations if _has_text(item.clarification_question)),
        None,
    )
    return RoutedResult(
        route=route,
        candidate_id=candidate_id_for(source_sha256, canonical.topic_key),
        topic_key=canonical.topic_key,
        type=canonical.type,
        intent=canonical.intent,
        title=canonical.title,
        component=canonical.component,
        summary=canonical.summary,
        requested_outcome=canonical.requested_outcome,
        acceptance_criteria=tuple(acceptance_criteria),
        clarification_question=clarification_question,
        confidence=confidence,
        evidence=tuple(evidence),
        evidence_frame_seconds=select_evidence_frame_timestamp(evidence),
        rationale=canonical.rationale,
        approval_eligible=route == "candidate",
        visually_inferred=visually_inferred,
        reason_code=reason_code,
    )


def route_analysis(
    analysis: AnalysisResult,
    *,
    duration_seconds: float,
    source_sha256: str,
) -> PolicyResult:
    observation_ids = [observation.observation_id for observation in analysis.observations]
    if len(observation_ids) != len(set(observation_ids)):
        raise PolicyFailure("observation IDs must be unique")

    groups: dict[str, list[Observation]] = defaultdict(list)
    normalized: dict[str, list[EvidenceSpan]] = defaultdict(list)
    for observation in analysis.observations:
        groups[observation.topic_key].append(observation)
        normalized[observation.topic_key].extend(_validate_observation(observation, duration_seconds))

    for topic_key, observations in groups.items():
        observations.sort(key=lambda observation: observation.observation_id)
        canonical = observations[0]
        expected = (canonical.type, canonical.component, canonical.intent, canonical.requested_outcome)
        if any((item.type, item.component, item.intent, item.requested_outcome) != expected for item in observations[1:]):
            raise PolicyFailure(f"duplicate topic group is inconsistent: {topic_key}")

    results = []
    for topic_key, observations in groups.items():
        evidence = sorted(
            normalized[topic_key],
            key=lambda span: (
                span.start_seconds,
                span.end_seconds,
                span.keyframe_seconds is None,
                span.keyframe_seconds or 0,
                span.client_quote or "",
                span.visual_observation or "",
            ),
        )
        results.append(_route_group(observations, evidence, source_sha256))
    return PolicyResult(schema_version="1.0", results=results)
