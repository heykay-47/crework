from typing import Any

import pytest
from pydantic import ValidationError

from feedback_triage.approval import (
    ApprovalError,
    build_approval,
    edit_candidate,
    marker_for,
    payload_hash,
    render_issue_payload,
)
from feedback_triage.models import RoutedResult


def candidate(**changes: Any) -> RoutedResult:
    value: dict[str, Any] = {
        "route": "candidate",
        "candidate_id": "cand_aaaaaaaaaaaaaaaa",
        "topic_key": "cta-label",
        "type": "change_request",
        "intent": "explicit_change",
        "title": "Change CTA label",
        "component": "hero",
        "summary": "The client asks for a clearer CTA label.",
        "requested_outcome": "Use Schedule a Call",
        "acceptance_criteria": ["The CTA reads Schedule a Call."],
        "clarification_question": None,
        "confidence": "high",
        "evidence": [
            {
                "start_seconds": 10.0,
                "end_seconds": 12.0,
                "keyframe_seconds": None,
                "client_quote": "Use Schedule a Call",
                "visual_observation": None,
            }
        ],
        "evidence_frame_seconds": 11.0,
        "rationale": "The requested change is explicit.",
        "approval_eligible": True,
        "visually_inferred": False,
        "reason_code": None,
    }
    value.update(changes)
    return RoutedResult.model_validate(value)


def test_rendered_payload_contains_one_stable_marker_and_hash() -> None:
    source = "b" * 64
    result = render_issue_payload(candidate(), source)

    marker = marker_for(source, candidate().candidate_id)
    assert result.body.count(marker) == 1
    assert payload_hash(result) != payload_hash(render_issue_payload(candidate(title="A different title"), source))
    assert marker in result.body
    assert "## Source evidence" in result.body


def test_approval_binds_exact_payload_and_destination() -> None:
    source = "b" * 64
    approved = build_approval(
        source,
        candidate(),
        "Demo/Feedback",
        operator_label="demo operator",
    )

    assert approved.source_sha256 == source
    assert approved.destination_repository == "demo/feedback"
    assert approved.payload_hash == payload_hash(approved.payload)
    assert approved.candidate_snapshot.evidence[0].start_seconds == 10.0


def test_approval_snapshot_nested_values_are_immutable() -> None:
    approved = build_approval("b" * 64, candidate(), "demo/feedback")

    with pytest.raises(ValidationError):
        approved.candidate_snapshot.acceptance_criteria += ("A second criterion.",)
    with pytest.raises(ValidationError):
        approved.candidate_snapshot.evidence[0].start_seconds = 11.0
    with pytest.raises(ValidationError):
        approved.payload.labels += ("triage",)


def test_manual_review_requires_confirmation_or_edit() -> None:
    manual = candidate(route="manual_review", approval_eligible=False, confidence="medium")

    with pytest.raises(ApprovalError, match="Manual Review"):
        build_approval("b" * 64, manual, "demo/feedback")
    with pytest.raises(ApprovalError, match="Manual Review"):
        build_approval("b" * 64, manual, "demo/feedback", changes={"title": manual.title + " "})
    with pytest.raises(ApprovalError, match="Manual Review"):
        build_approval("b" * 64, manual, "demo/feedback", changes={"summary": manual.summary})
    with pytest.raises(ApprovalError, match="Manual Review"):
        build_approval("b" * 64, manual, "demo/feedback", changes={"summary": f"  {manual.summary}  "})

    approved = build_approval(
        "b" * 64,
        manual,
        "demo/feedback",
        manual_review_confirmed=True,
    )
    assert approved.manual_review_confirmed is True

    edited = build_approval(
        "b" * 64,
        manual,
        "demo/feedback",
        changes={"title": "A materially different title"},
    )
    assert edited.manual_review_confirmed is True


def test_candidate_edits_never_persist_manual_review_confirmation() -> None:
    approved = build_approval(
        "b" * 64,
        candidate(),
        "demo/feedback",
        changes={"title": "A materially different title"},
        manual_review_confirmed=True,
    )

    assert approved.manual_review_confirmed is False


def test_clarification_and_withheld_results_cannot_be_approved() -> None:
    blocked_results = (
        candidate(
            route="clarification_request",
            approval_eligible=False,
            clarification_question="Which CTA should be changed?",
        ),
        candidate(route="withheld_result", approval_eligible=False, reason_code="low_confidence"),
    )
    for blocked in blocked_results:
        with pytest.raises(ApprovalError, match="cannot be approved"):
            build_approval("b" * 64, blocked, "demo/feedback")


def test_only_prose_fields_can_be_edited() -> None:
    original = candidate()
    edited = edit_candidate(original, {"title": "Use the scheduling CTA"})

    assert edited.title == "Use the scheduling CTA"
    assert edited.candidate_id == original.candidate_id
    assert edited.evidence == original.evidence

    with pytest.raises(ApprovalError, match="immutable"):
        edit_candidate(original, {"evidence": []})
