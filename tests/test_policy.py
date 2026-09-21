from copy import deepcopy
from typing import Any

import pytest

from feedback_triage.models import AnalysisResult
from feedback_triage.policy import PolicyFailure, route_analysis


def observation(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "observation_id": "obs_001",
        "topic_key": "cta-label",
        "type": "change_request",
        "intent": "explicit_change",
        "title": "Change CTA label",
        "component": "hero",
        "summary": "The client asks for a clearer CTA label.",
        "requested_outcome": "Use Start free trial",
        "acceptance_criteria": ["CTA reads Start free trial"],
        "clarification_question": None,
        "confidence": "high",
        "evidence": [
            {
                "start_seconds": 1.0,
                "end_seconds": 2.0,
                "keyframe_seconds": None,
                "client_quote": "Use Start free trial",
                "visual_observation": None,
            }
        ],
        "rationale": "The request is explicit.",
    }
    value.update(changes)
    return value


def analysis(*observations: dict[str, Any]) -> AnalysisResult:
    return AnalysisResult.model_validate(
        {
            "schema_version": "1.0",
            "video_summary": "The client gives product feedback.",
            "observations": [deepcopy(item) for item in observations],
        }
    )


def test_high_confidence_explicit_change_is_approval_eligible_candidate() -> None:
    result = route_analysis(analysis(observation()), duration_seconds=10.0, source_sha256="a" * 64)

    assert len(result.results) == 1
    candidate = result.results[0]
    assert candidate.route == "candidate"
    assert candidate.candidate_id == "cand_ca4e1fad42466e7b"
    assert candidate.approval_eligible is True
    assert candidate.topic_key == "cta-label"


def test_low_confidence_precedes_visual_only_manual_review() -> None:
    visual_anomaly = observation(
        topic_key="mobile-overflow",
        type="bug",
        intent="none",
        requested_outcome=None,
        acceptance_criteria=[],
        confidence="low",
        evidence=[
            {
                "start_seconds": 4.0,
                "end_seconds": 5.0,
                "keyframe_seconds": 4.5,
                "client_quote": None,
                "visual_observation": "The mobile page extends past the viewport.",
            }
        ],
    )

    result = route_analysis(analysis(visual_anomaly), duration_seconds=10.0, source_sha256="a" * 64)

    withheld = result.results[0]
    assert withheld.route == "withheld_result"
    assert withheld.reason_code == "low_confidence"
    assert withheld.approval_eligible is False
    assert withheld.visually_inferred is True


def test_question_is_withheld_as_not_a_request() -> None:
    question = observation(
        topic_key="analytics-question",
        type="question",
        intent="question",
        requested_outcome=None,
        acceptance_criteria=[],
        confidence="high",
    )

    result = route_analysis(analysis(question), duration_seconds=10.0, source_sha256="a" * 64)

    withheld = result.results[0]
    assert withheld.route == "withheld_result"
    assert withheld.reason_code == "question_not_request"
    assert withheld.approval_eligible is False


def test_ambiguous_reaction_requests_clarification() -> None:
    reaction = observation(
        topic_key="pricing-reaction",
        type="reaction",
        intent="ambiguous_reaction",
        requested_outcome=None,
        acceptance_criteria=[],
        clarification_question="What should change about the pricing section?",
        confidence="high",
    )

    result = route_analysis(analysis(reaction), duration_seconds=10.0, source_sha256="a" * 64)

    clarification = result.results[0]
    assert clarification.route == "clarification_request"
    assert clarification.reason_code is None
    assert clarification.approval_eligible is False


def test_visual_only_possible_bug_requires_manual_review() -> None:
    visual_anomaly = observation(
        topic_key="mobile-overflow",
        type="bug",
        intent="none",
        requested_outcome=None,
        acceptance_criteria=[],
        confidence="medium",
        evidence=[
            {
                "start_seconds": 4.0,
                "end_seconds": 5.0,
                "keyframe_seconds": 4.5,
                "client_quote": None,
                "visual_observation": "The mobile page extends past the viewport.",
            }
        ],
    )

    result = route_analysis(analysis(visual_anomaly), duration_seconds=10.0, source_sha256="a" * 64)

    review = result.results[0]
    assert review.route == "manual_review"
    assert review.approval_eligible is False
    assert review.visually_inferred is True


def test_medium_confidence_actionable_observation_requires_manual_review() -> None:
    medium_change = observation(confidence="medium")

    result = route_analysis(analysis(medium_change), duration_seconds=10.0, source_sha256="a" * 64)

    review = result.results[0]
    assert review.route == "manual_review"
    assert review.approval_eligible is False
    assert review.visually_inferred is False


def test_explicit_spoken_and_visible_bug_is_approval_eligible_not_visually_inferred() -> None:
    explicit_bug = observation(
        topic_key="navbar-overlap",
        type="bug",
        intent="explicit_problem",
        requested_outcome="The navbar must not overlap the heading.",
        acceptance_criteria=["The navbar does not overlap the heading."],
        confidence="high",
        evidence=[
            {
                "start_seconds": 3.0,
                "end_seconds": 4.0,
                "keyframe_seconds": 3.5,
                "client_quote": "The navbar is covering the heading.",
                "visual_observation": "The navbar overlaps the heading.",
            }
        ],
    )

    result = route_analysis(analysis(explicit_bug), duration_seconds=10.0, source_sha256="a" * 64)

    candidate = result.results[0]
    assert candidate.route == "candidate"
    assert candidate.approval_eligible is True
    assert candidate.visually_inferred is False


def test_duplicate_topic_merges_sorted_normalized_evidence_and_keeps_candidate_identity() -> None:
    first = observation(
        observation_id="obs_002",
        topic_key="navbar-overlap",
        type="bug",
        intent="explicit_problem",
        requested_outcome="The navbar must not overlap the heading.",
        acceptance_criteria=["The navbar does not overlap the heading."],
        evidence=[
            {
                "start_seconds": 9.0,
                "end_seconds": 10.4,
                "keyframe_seconds": 9.5,
                "client_quote": None,
                "visual_observation": "The navbar overlaps the heading.",
            }
        ],
    )
    repeated = observation(
        observation_id="obs_001",
        topic_key="navbar-overlap",
        type="bug",
        intent="explicit_problem",
        requested_outcome="The navbar must not overlap the heading.",
        acceptance_criteria=["The navbar does not overlap the heading."],
        evidence=[
            {
                "start_seconds": 2.0,
                "end_seconds": 3.0,
                "keyframe_seconds": None,
                "client_quote": "The navbar is covering the heading.",
                "visual_observation": None,
            }
        ],
    )

    result = route_analysis(analysis(first, repeated), duration_seconds=10.0, source_sha256="a" * 64)

    assert len(result.results) == 1
    candidate = result.results[0]
    assert candidate.candidate_id == "cand_bcfea10d007cdb52"
    assert [(span.start_seconds, span.end_seconds) for span in candidate.evidence] == [(2.0, 3.0), (9.0, 10.0)]
    assert candidate.evidence_frame_seconds == 9.5


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"topic_key": "Not Kebab Case"}, "invalid topic key"),
        ({"type": "bug", "intent": "explicit_change"}, "explicit_change"),
        ({"type": "reaction", "intent": "ambiguous_reaction", "requested_outcome": "Redesign it"}, "ambiguous_reaction"),
        (
            {
                "type": "commentary",
                "intent": "ambiguous_reaction",
                "requested_outcome": None,
                "clarification_question": "What should change?",
            },
            "ambiguous_reaction requires type reaction, not commentary",
        ),
        ({"type": "commentary", "intent": "none", "acceptance_criteria": ["Change it"]}, "empty acceptance"),
    ],
)
def test_cross_field_policy_failures_stop_the_whole_analysis(changes: dict[str, object], message: str) -> None:
    invalid = observation(observation_id="obs_002", **changes)

    with pytest.raises(PolicyFailure, match=message):
        route_analysis(analysis(observation(topic_key="valid-topic"), invalid), duration_seconds=10.0, source_sha256="a" * 64)


def test_policy_failure_names_the_offending_observation() -> None:
    invalid = observation(observation_id="obs_002", type="commentary", intent="ambiguous_reaction")

    with pytest.raises(PolicyFailure, match="^obs_002: "):
        route_analysis(analysis(observation(topic_key="valid-topic"), invalid), duration_seconds=10.0, source_sha256="a" * 64)


def test_explicit_change_requires_client_stated_evidence() -> None:
    visual_only_change = observation(
        evidence=[
            {
                "start_seconds": 1.0,
                "end_seconds": 2.0,
                "keyframe_seconds": 1.5,
                "client_quote": None,
                "visual_observation": "The current CTA reads Sign up.",
            }
        ]
    )

    with pytest.raises(PolicyFailure, match="client-stated evidence"):
        route_analysis(analysis(visual_only_change), duration_seconds=10.0, source_sha256="a" * 64)


def test_duplicate_observation_ids_fail_the_whole_analysis() -> None:
    duplicate_id = observation(topic_key="second-topic")

    with pytest.raises(PolicyFailure, match="IDs must be unique"):
        route_analysis(analysis(observation(), duplicate_id), duration_seconds=10.0, source_sha256="a" * 64)


def test_incompatible_duplicate_topic_group_fails_the_whole_analysis() -> None:
    duplicate = observation(observation_id="obs_002", component="footer")

    with pytest.raises(PolicyFailure, match="duplicate topic group is inconsistent"):
        route_analysis(analysis(observation(), duplicate), duration_seconds=10.0, source_sha256="a" * 64)


@pytest.mark.parametrize(
    "evidence",
    [
        {"start_seconds": -0.1, "end_seconds": 1.0, "keyframe_seconds": None, "client_quote": "Change it", "visual_observation": None},
        {"start_seconds": 2.0, "end_seconds": 1.0, "keyframe_seconds": None, "client_quote": "Change it", "visual_observation": None},
        {"start_seconds": 10.1, "end_seconds": 10.2, "keyframe_seconds": None, "client_quote": "Change it", "visual_observation": None},
        {"start_seconds": 9.0, "end_seconds": 10.6, "keyframe_seconds": None, "client_quote": "Change it", "visual_observation": None},
        {"start_seconds": 9.0, "end_seconds": 10.4, "keyframe_seconds": 10.2, "client_quote": "Change it", "visual_observation": None},
    ],
)
def test_timestamp_policy_failures_stop_the_whole_analysis(evidence: dict[str, object]) -> None:
    invalid = observation(evidence=[evidence])

    with pytest.raises(PolicyFailure, match="evidence"):
        route_analysis(analysis(invalid), duration_seconds=10.0, source_sha256="a" * 64)


def test_timestamp_failure_identifies_observation_span_and_duration() -> None:
    invalid = observation(
        observation_id="obs_002",
        evidence=[{
            "start_seconds": 210.0,
            "end_seconds": 218.0,
            "keyframe_seconds": None,
            "client_quote": "Same issue down here",
            "visual_observation": None,
        }],
    )

    with pytest.raises(
        PolicyFailure,
        match=r"^obs_002: evidence span 210\.0-218\.0s exceeds the Feedback Recording duration of 139\.766667s$",
    ):
        route_analysis(analysis(invalid), duration_seconds=139.766667, source_sha256="a" * 64)
