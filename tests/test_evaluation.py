from typing import Any

from feedback_triage.evaluation import GroundTruthManifest, score_policy_result
from feedback_triage.models import EvidenceSpan, PolicyResult, RoutedResult


def manifest() -> GroundTruthManifest:
    return GroundTruthManifest.model_validate(
        {
            "fixture_version": "canonical-six-case-v1",
            "source_path": "feedback-recording.mp4",
            "source_sha256": "a" * 64,
            "duration_seconds": 360.0,
            "cases": [
                {
                    "case_id": "A",
                    "label": "hero CTA copy change",
                    "authored_window": {"start_seconds": 25.0, "end_seconds": 45.0},
                    "required": {
                        "topic_key": "hero-cta-copy",
                        "type": "change_request",
                        "intent": "explicit_change",
                        "route": "candidate",
                        "reason_code": None,
                        "approval_eligible": True,
                        "visually_inferred": False,
                        "requested_outcome": {"presence": "required", "required_terms": ["requested outcome"]},
                        "acceptance_criteria": {"presence": "forbidden"},
                        "clarification_question": {"presence": "forbidden"},
                    },
                    "forbidden": {"routes": ["clarification_request", "manual_review", "withheld_result"]},
                    "evidence_frame_required": False,
                    "client_quote": {"presence": "required", "required_terms": ["grounded"]},
                },
                {
                    "case_id": "B",
                    "label": "spoken and visible navbar overlap",
                    "authored_window": {"start_seconds": 75.0, "end_seconds": 95.0},
                    "required": {
                        "topic_key": "navbar-overlap",
                        "type": "bug",
                        "intent": "explicit_problem",
                        "route": "candidate",
                        "reason_code": None,
                        "approval_eligible": True,
                        "visually_inferred": False,
                        "candidate_id": "cand_bbbbbbbbbbbbbbbb",
                        "requested_outcome": {"presence": "required", "required_terms": ["logo"]},
                        "acceptance_criteria": {"presence": "forbidden"},
                    },
                    "forbidden": {"routes": ["clarification_request", "manual_review", "withheld_result"]},
                    "evidence_frame_required": True,
                    "client_quote": {"presence": "required", "required_terms": ["menu", "logo"]},
                    "visual_observation": {"presence": "required", "required_terms": ["menu", "logo"]},
                },
                {
                    "case_id": "C",
                    "label": "vague pricing reaction",
                    "authored_window": {"start_seconds": 145.0, "end_seconds": 165.0},
                    "required": {
                        "topic_key": "pricing-reaction",
                        "type": "reaction",
                        "intent": "ambiguous_reaction",
                        "route": "clarification_request",
                        "reason_code": None,
                        "approval_eligible": False,
                        "visually_inferred": False,
                        "requested_outcome": {"presence": "forbidden"},
                        "acceptance_criteria": {"presence": "forbidden"},
                        "clarification_question": {"presence": "required", "required_terms": ["pricing"]},
                    },
                    "forbidden": {"routes": ["candidate", "manual_review", "withheld_result"]},
                    "evidence_frame_required": False,
                    "client_quote": {"presence": "required", "required_terms": ["feels"]},
                },
                {
                    "case_id": "D",
                    "label": "repeated navbar mention",
                    "authored_window": {"start_seconds": 205.0, "end_seconds": 225.0},
                    "required": {
                        "topic_key": "navbar-overlap",
                        "type": "bug",
                        "intent": "explicit_problem",
                        "route": "candidate",
                        "reason_code": None,
                        "approval_eligible": True,
                        "visually_inferred": False,
                        "candidate_id": "cand_bbbbbbbbbbbbbbbb",
                        "requested_outcome": {"presence": "required", "required_terms": ["logo"]},
                        "acceptance_criteria": {"presence": "forbidden"},
                    },
                    "forbidden": {"routes": ["clarification_request", "manual_review", "withheld_result"]},
                    "evidence_frame_required": False,
                    "client_quote": {"presence": "required", "required_terms": ["menu", "logo"]},
                    "visual_observation": {"presence": "required", "required_terms": ["menu", "logo"]},
                },
                {
                    "case_id": "E",
                    "label": "analytics tracking question",
                    "authored_window": {"start_seconds": 255.0, "end_seconds": 275.0},
                    "required": {
                        "topic_key": "analytics-question",
                        "type": "question",
                        "intent": "question",
                        "route": "withheld_result",
                        "reason_code": "question_not_request",
                        "approval_eligible": False,
                        "visually_inferred": False,
                        "requested_outcome": {"presence": "forbidden"},
                        "acceptance_criteria": {"presence": "forbidden"},
                        "clarification_question": {"presence": "forbidden"},
                    },
                    "forbidden": {"routes": ["candidate", "manual_review", "clarification_request"]},
                    "evidence_frame_required": False,
                    "client_quote": {"presence": "required", "required_terms": ["analytics", "button"]},
                },
                {
                    "case_id": "F",
                    "label": "visual-only anomaly",
                    "authored_window": {"start_seconds": 305.0, "end_seconds": 325.0},
                    "required": {
                        "topic_key": "contact-button-unresponsive",
                        "type": "bug",
                        "intent": "none",
                        "route": "manual_review",
                        "reason_code": None,
                        "approval_eligible": False,
                        "visually_inferred": True,
                        "requested_outcome": {"presence": "forbidden"},
                        "acceptance_criteria": {"presence": "forbidden"},
                        "clarification_question": {"presence": "forbidden"},
                    },
                    "forbidden": {"routes": ["candidate", "clarification_request", "withheld_result"]},
                    "evidence_frame_required": True,
                    "client_quote": {"presence": "forbidden"},
                    "visual_observation": {"presence": "required", "required_terms": ["button"]},
                },
            ],
            "duplicate_relationships": [
                {
                    "case_ids": ["B", "D"],
                    "topic_key": "navbar-overlap",
                    "candidate_id": "cand_bbbbbbbbbbbbbbbb",
                }
            ],
        }
    )


def routed(**changes: Any) -> RoutedResult:
    value: dict[str, Any] = {
        "route": "candidate",
        "candidate_id": "cand_aaaaaaaaaaaaaaaa",
        "topic_key": "hero-cta-copy",
        "type": "change_request",
        "intent": "explicit_change",
        "title": "A result",
        "component": "hero",
        "summary": "A result summary.",
        "requested_outcome": "A requested outcome.",
        "acceptance_criteria": [],
        "clarification_question": None,
        "confidence": "high",
        "evidence": [
            EvidenceSpan(
                start_seconds=30.0,
                end_seconds=40.0,
                client_quote="A grounded quote.",
                visual_observation=None,
            )
        ],
        "evidence_frame_seconds": 35.0,
        "rationale": "The result is grounded.",
        "approval_eligible": True,
        "visually_inferred": False,
        "reason_code": None,
    }
    value.update(changes)
    return RoutedResult.model_validate(value)


def canonical_policy() -> PolicyResult:
    return PolicyResult(
        schema_version="1.0",
        results=[
            routed(topic_key="hero-cta-copy", candidate_id="cand_aaaaaaaaaaaaaaaa"),
            routed(
                topic_key="navbar-overlap",
                candidate_id="cand_bbbbbbbbbbbbbbbb",
                type="bug",
                intent="explicit_problem",
                component="navbar",
                evidence=[
                    EvidenceSpan(
                        start_seconds=80.0,
                        end_seconds=90.0,
                        client_quote="The menu covers the logo.",
                        visual_observation="The mobile menu overlaps the logo.",
                    ),
                    EvidenceSpan(
                        start_seconds=210.0,
                        end_seconds=220.0,
                        client_quote="This menu covers the logo here too.",
                        visual_observation="The mobile menu overlaps the logo again.",
                    ),
                ],
                evidence_frame_seconds=85.0,
                requested_outcome="Keep the logo unobstructed.",
                acceptance_criteria=[],
                visually_inferred=False,
            ),
            routed(
                topic_key="pricing-reaction",
                candidate_id="cand_cccccccccccccccc",
                type="reaction",
                intent="ambiguous_reaction",
                route="clarification_request",
                requested_outcome=None,
                acceptance_criteria=[],
                clarification_question="What should change about pricing?",
                evidence=[
                    EvidenceSpan(
                        start_seconds=150.0,
                        end_seconds=160.0,
                        client_quote="Something feels off.",
                        visual_observation=None,
                    )
                ],
                evidence_frame_seconds=155.0,
                approval_eligible=False,
            ),
            routed(
                topic_key="analytics-question",
                candidate_id="cand_dddddddddddddddd",
                type="question",
                intent="question",
                route="withheld_result",
                requested_outcome=None,
                acceptance_criteria=[],
                evidence=[
                    EvidenceSpan(
                        start_seconds=260.0,
                        end_seconds=270.0,
                        client_quote="Is analytics tracking this button?",
                        visual_observation=None,
                    )
                ],
                evidence_frame_seconds=265.0,
                approval_eligible=False,
                reason_code="question_not_request",
            ),
            routed(
                topic_key="contact-button-unresponsive",
                candidate_id="cand_eeeeeeeeeeeeeeee",
                type="bug",
                intent="none",
                route="manual_review",
                requested_outcome=None,
                acceptance_criteria=[],
                evidence=[
                    EvidenceSpan(
                        start_seconds=310.0,
                        end_seconds=320.0,
                        client_quote=None,
                        visual_observation="The contact button does not visibly respond.",
                    )
                ],
                evidence_frame_seconds=315.0,
                approval_eligible=False,
                visually_inferred=True,
            ),
        ],
    )


def test_canonical_scorer_accepts_all_six_authored_cases_and_duplicate_identity() -> None:
    report = score_policy_result(canonical_policy(), manifest())

    assert report.passed is True
    assert report.matched_case_ids == ["A", "B", "C", "D", "E", "F"]
    assert report.actionable_result_count == 4
    assert report.errors == []


def test_canonical_scorer_rejects_missing_duplicate_window_and_invented_outcome() -> None:
    policy = canonical_policy()
    navbar = policy.results[1].model_copy(update={"evidence": policy.results[1].evidence[:1]})
    visual = policy.results[-1].model_copy(update={"requested_outcome": "Make the button submit."})
    policy = policy.model_copy(update={"results": [policy.results[0], navbar, *policy.results[2:-1], visual]})

    report = score_policy_result(policy, manifest())

    assert report.passed is False
    assert "D: no Evidence Span midpoint falls inside its authored window" in report.errors
    assert "F: unsupported requested outcome was invented" in report.errors


def test_canonical_scorer_rejects_unsupported_required_outcome_terms_and_wrong_topic_window() -> None:
    policy = canonical_policy()
    navbar = policy.results[1].model_copy(
        update={
            "requested_outcome": "Keep the logo unobstructed.",
            "evidence": [
                policy.results[1].evidence[0],
                EvidenceSpan(
                    start_seconds=150.0,
                    end_seconds=160.0,
                    client_quote="The navbar issue is here too.",
                    visual_observation=None,
                ),
            ],
        }
    )
    hero = policy.results[0].model_copy(update={"requested_outcome": "Delete the CTA."})
    policy = policy.model_copy(update={"results": [hero, navbar, *policy.results[2:]]})

    report = score_policy_result(policy, manifest())

    assert report.passed is False
    assert "A: required requested outcome term 'requested outcome' is missing" in report.errors
    assert "D: no Evidence Span midpoint falls inside its authored window" in report.errors
    assert "navbar-overlap: Evidence Span midpoint is outside its authored window" in report.errors


def test_canonical_scorer_rejects_evidence_without_grounded_modality_terms() -> None:
    policy = canonical_policy()
    navbar = policy.results[1].model_copy(
        update={
            "evidence": [
                EvidenceSpan(
                    start_seconds=80.0,
                    end_seconds=90.0,
                    client_quote="The menu covers the logo.",
                    visual_observation="The footer is red.",
                ),
                EvidenceSpan(
                    start_seconds=210.0,
                    end_seconds=220.0,
                    client_quote="This navbar problem happens here too.",
                    visual_observation="The dashboard footer is red again.",
                ),
            ]
        }
    )
    policy = policy.model_copy(update={"results": [policy.results[0], navbar, *policy.results[2:]]})

    report = score_policy_result(policy, manifest())

    assert report.passed is False
    assert "B: required visual evidence term 'menu' is missing" in report.errors
