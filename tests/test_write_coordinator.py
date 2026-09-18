import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from feedback_triage.approval import build_approval, marker_for
from feedback_triage.github import GitHubApiError, GitHubIssue, GitHubTransportError
from feedback_triage.ledger import LedgerLocked, RunLedger
from feedback_triage.models import RoutedResult
from feedback_triage.writes import ExternalWriteFailure, ReviewDecision, WriteCoordinator


def candidate(candidate_id: str = "cand_aaaaaaaaaaaaaaaa") -> RoutedResult:
    return RoutedResult.model_validate(
        {
            "route": "candidate",
            "candidate_id": candidate_id,
            "topic_key": "cta",
            "type": "change_request",
            "intent": "explicit_change",
            "title": "Change CTA",
            "component": "hero",
            "summary": "Change the CTA label.",
            "requested_outcome": "Use Schedule a Call",
            "acceptance_criteria": ["The CTA uses the requested label."],
            "clarification_question": None,
            "confidence": "high",
            "evidence": [
                {
                    "start_seconds": 1.0,
                    "end_seconds": 2.0,
                    "keyframe_seconds": None,
                    "client_quote": "Schedule a Call",
                    "visual_observation": None,
                }
            ],
            "evidence_frame_seconds": 1.5,
            "rationale": "Explicit request.",
            "approval_eligible": True,
            "visually_inferred": False,
            "reason_code": None,
        }
    )


def ledger(tmp_path: Path) -> tuple[RunLedger, str]:
    value = RunLedger.create(
        tmp_path,
        source_sha256="a" * 64,
        fingerprint="b" * 64,
        fingerprint_inputs={"source": "a" * 64},
    )
    attempt_id = value.start_attempt()
    value.complete(attempt_id, {"observations": []}, processing_pair_count=1)
    value.record_policy_result(
        attempt_id,
        {
            "schema_version": "1.0",
            "results": [
                candidate(candidate_id).model_dump(mode="json")
                for candidate_id in (
                    "cand_aaaaaaaaaaaaaaaa",
                    "cand_bbbbbbbbbbbbbbbb",
                    "cand_cccccccccccccccc",
                )
            ],
        },
    )
    return value, attempt_id


def approved(ledger_value: RunLedger) -> Any:
    return build_approval("a" * 64, candidate(), "demo/feedback", approved_at="2026-01-01T00:00:00+00:00")


@dataclass
class FakeGitHub:
    matches: list[GitHubIssue] = field(default_factory=list)
    issues: dict[int, GitHubIssue] = field(default_factory=dict)
    create_results: list[GitHubIssue | Exception] = field(default_factory=list)
    find_calls: int = 0
    create_calls: int = 0

    def find_marker(self, destination_repository: str, marker: str) -> tuple[GitHubIssue, ...]:
        self.find_calls += 1
        return tuple(issue for issue in self.matches if issue.body.count(marker) == 1)

    def get_issue(self, destination_repository: str, issue_number: int) -> GitHubIssue:
        return self.issues[issue_number]

    def create_issue(self, destination_repository: str, payload: Any) -> GitHubIssue:
        self.create_calls += 1
        result = self.create_results.pop(0)
        if isinstance(result, Exception):
            raise result
        self.issues[result.number] = result
        return result


def remote_issue(approval: Any, number: int = 7, state: str = "open") -> GitHubIssue:
    return GitHubIssue(
        destination_repository=approval.destination_repository,
        number=number,
        title=approval.payload.title,
        body=approval.payload.body,
        html_url=f"https://github.com/{approval.destination_repository}/issues/{number}",
        state=state,
    )


def test_existing_exact_marker_is_adopted_without_post(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    remote = remote_issue(approval, state="closed")
    github = FakeGitHub(matches=[remote])

    outcomes = WriteCoordinator(ledger_value, github).publish([approval], attempt_id=attempt_id)

    assert outcomes[0].state == "adopted"
    assert outcomes[0].issue_record is not None
    assert github.create_calls == 0
    assert ledger_value.issue_records[0].issue_number == 7


def test_create_persists_pending_before_post_and_written_record_after_verification(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    github = FakeGitHub(create_results=[remote_issue(approval)])

    outcomes = WriteCoordinator(ledger_value, github).publish([approval], attempt_id=attempt_id)

    assert outcomes[0].state == "created"
    assert [event["state"] for event in ledger_value.write_attempts] == ["write_pending", "written"]
    assert ledger_value.issue_records[0].candidate_id == approval.candidate_id


def test_verified_record_is_reused_on_rerun_without_new_search_or_post(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    github = FakeGitHub(create_results=[remote_issue(approval)])
    coordinator = WriteCoordinator(ledger_value, github)

    coordinator.publish([approval], attempt_id=attempt_id)
    first_find_calls = github.find_calls
    outcomes = coordinator.publish([approval], attempt_id=attempt_id)

    assert outcomes[0].state == "skipped"
    assert github.find_calls == first_find_calls
    assert github.create_calls == 1


def test_missing_issue_record_is_a_conflict_not_permission_to_recreate(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    github = FakeGitHub(create_results=[remote_issue(approval, 8)])
    WriteCoordinator(ledger_value, github).publish([approval], attempt_id=attempt_id)

    saved = json.loads(ledger_value.path.read_text())
    saved["issue_records"] = []
    ledger_value.path.write_text(json.dumps(saved))
    reloaded = RunLedger.load(ledger_value.path)

    with pytest.raises(ExternalWriteFailure) as raised:
        WriteCoordinator(reloaded, github).publish([approval], attempt_id=attempt_id)

    assert raised.value.code == "external_write_conflict"
    assert github.create_calls == 1
    saved_after = json.loads(reloaded.path.read_text())
    assert saved_after["reconciliation_errors"][-1]["code"] == "external_write_conflict"


def test_multiple_exact_markers_block_the_batch(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    first = approved(ledger_value)
    second = build_approval(
        "a" * 64,
        candidate("cand_bbbbbbbbbbbbbbbb"),
        "demo/feedback",
        approved_at="2026-01-01T00:00:01+00:00",
    )
    marker = marker_for(first.source_sha256, first.candidate_id)
    github = FakeGitHub(
        matches=[remote_issue(first, 1), remote_issue(first, 2)],
        create_results=[remote_issue(second, 3)],
    )

    with pytest.raises(ExternalWriteFailure) as raised:
        WriteCoordinator(ledger_value, github).publish([first, second], attempt_id=attempt_id)

    assert raised.value.code == "external_write_conflict"
    assert github.create_calls == 0
    assert marker in github.matches[0].body


def test_review_only_prompts_admitted_routes_and_persists_manual_edit(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    manual = candidate("cand_bbbbbbbbbbbbbbbb").model_copy(
        update={"route": "manual_review", "approval_eligible": False, "confidence": "medium"}
    )
    clarification = candidate("cand_cccccccccccccccc").model_copy(
        update={
            "route": "clarification_request",
            "approval_eligible": False,
            "clarification_question": "Which CTA should change?",
        }
    )
    manual_approval = build_approval(
        "a" * 64,
        manual,
        "demo/feedback",
        changes={"title": "Edited manual review"},
        approved_at="2026-01-01T00:00:00+00:00",
    )
    candidate_approval = approved(ledger_value)
    ledger_value.record_policy_result(
        attempt_id,
        {
            "schema_version": "1.0",
            "results": [
                clarification.model_dump(mode="json"),
                manual.model_dump(mode="json"),
                candidate().model_dump(mode="json"),
            ],
        },
    )
    github = FakeGitHub(create_results=[remote_issue(manual_approval, 8), remote_issue(candidate_approval, 9)])
    seen: list[str] = []

    def decide(value: RoutedResult) -> ReviewDecision:
        seen.append(value.route)
        if value.route == "candidate":
            return ReviewDecision("approve")
        return ReviewDecision("approve", {"title": "Edited manual review"})

    outcomes = WriteCoordinator(ledger_value, github).review_and_publish(
        [clarification, manual, candidate()],
        source_sha256="a" * 64,
        destination_repository="demo/feedback",
        attempt_id=attempt_id,
        decision_fn=decide,
    )

    assert seen == ["manual_review", "candidate"]
    assert {outcome.state for outcome in outcomes} == {"created"}
    assert [approval.payload.title for approval in ledger_value.approvals] == [
        "Edited manual review",
        "Change CTA",
    ]


def test_definitive_failure_is_not_retried_without_explicit_action(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    github = FakeGitHub(create_results=[GitHubApiError("rejected", status_code=422, definitive=True)])
    coordinator = WriteCoordinator(ledger_value, github)

    with pytest.raises(ExternalWriteFailure) as raised:
        coordinator.publish([approval], attempt_id=attempt_id)
    assert raised.value.code == "external_write_failed"
    assert [event["state"] for event in ledger_value.write_attempts] == ["write_pending", "write_failed"]

    with pytest.raises(ExternalWriteFailure):
        coordinator.publish([approval], attempt_id=attempt_id)
    assert github.create_calls == 1

    github.create_results.append(remote_issue(approval, 12))
    outcomes = coordinator.publish([approval], attempt_id=attempt_id, retry_failed=True)
    assert outcomes[0].state == "created"
    assert github.create_calls == 2


def test_exact_marker_after_definitive_failure_is_adopted_without_retry(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    github = FakeGitHub(create_results=[GitHubApiError("rejected", status_code=422, definitive=True)])
    coordinator = WriteCoordinator(ledger_value, github)

    with pytest.raises(ExternalWriteFailure):
        coordinator.publish([approval], attempt_id=attempt_id)

    github.matches.append(remote_issue(approval, 18))
    outcomes = coordinator.publish([approval], attempt_id=attempt_id)

    assert outcomes[0].state == "adopted"
    assert github.create_calls == 1


def test_lost_create_response_is_reconciled_to_one_existing_match(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    first_gateway = FakeGitHub(create_results=[GitHubTransportError("connection lost")])
    coordinator = WriteCoordinator(ledger_value, first_gateway)

    with pytest.raises(ExternalWriteFailure) as raised:
        coordinator.publish([approval], attempt_id=attempt_id)
    assert raised.value.code == "external_write_uncertain"

    adopted_gateway = FakeGitHub(matches=[remote_issue(approval, 13)])
    outcomes = WriteCoordinator(ledger_value, adopted_gateway).publish([approval], attempt_id=attempt_id)

    assert outcomes[0].state == "skipped"
    assert adopted_gateway.create_calls == 0
    assert ledger_value.issue_records[0].issue_number == 13


def test_uncertain_zero_match_never_auto_retries_and_requires_override(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    github = FakeGitHub(create_results=[GitHubTransportError("request may not have arrived")])
    coordinator = WriteCoordinator(ledger_value, github)

    with pytest.raises(ExternalWriteFailure):
        coordinator.publish([approval], attempt_id=attempt_id)
    with pytest.raises(ExternalWriteFailure) as raised:
        coordinator.publish([approval], attempt_id=attempt_id)
    assert raised.value.code == "external_write_uncertain"
    assert github.create_calls == 1

    github.create_results.append(remote_issue(approval, 14))
    outcomes = coordinator.publish(
        [approval],
        attempt_id=attempt_id,
        retry_uncertain=True,
        confirm_no_issue=True,
    )
    assert outcomes[0].state == "created"
    assert github.create_calls == 2

    rerun = coordinator.publish([approval], attempt_id=attempt_id)
    assert rerun[0].state == "skipped"


def test_partial_batch_stops_before_later_candidates(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    first = approved(ledger_value)
    second = build_approval(
        "a" * 64,
        candidate("cand_bbbbbbbbbbbbbbbb"),
        "demo/feedback",
        approved_at="2026-01-01T00:00:01+00:00",
    )
    third = build_approval(
        "a" * 64,
        candidate("cand_cccccccccccccccc"),
        "demo/feedback",
        approved_at="2026-01-01T00:00:02+00:00",
    )
    github = FakeGitHub(create_results=[remote_issue(first, 15), GitHubApiError("rejected", status_code=422, definitive=True), remote_issue(third, 17)])

    with pytest.raises(ExternalWriteFailure):
        WriteCoordinator(ledger_value, github).publish([first, second, third], attempt_id=attempt_id)

    assert github.create_calls == 2
    assert [record.candidate_id for record in ledger_value.issue_records] == [first.candidate_id]


def test_source_lock_blocks_a_second_writer_before_approval(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    github = FakeGitHub(create_results=[remote_issue(approval, 18)])
    coordinator = WriteCoordinator(ledger_value, github)

    with ledger_value.exclusive_lock():
        with pytest.raises(LedgerLocked):
            coordinator.publish([approval], attempt_id=attempt_id)
    assert ledger_value.approvals == ()


def test_unchanged_persisted_approval_resumes_without_prompt(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    ledger_value.record_approval(attempt_id, approval)
    github = FakeGitHub(matches=[remote_issue(approval, 19)])

    def should_not_prompt(_: RoutedResult) -> ReviewDecision:
        raise AssertionError("an unchanged persisted Approval should be resumed")

    outcomes = WriteCoordinator(ledger_value, github).review_and_publish(
        [candidate()],
        source_sha256="a" * 64,
        destination_repository="demo/feedback",
        attempt_id=attempt_id,
        decision_fn=should_not_prompt,
    )

    assert outcomes[0].state == "adopted"
    assert github.create_calls == 0


def test_review_rejects_candidate_not_in_persisted_policy(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    unpersisted = candidate("cand_dddddddddddddddd")
    github = FakeGitHub()

    with pytest.raises(ValueError, match="persisted policy"):
        WriteCoordinator(ledger_value, github).review_and_publish(
            [unpersisted],
            source_sha256="a" * 64,
            destination_repository="demo/feedback",
            attempt_id=attempt_id,
            decision_fn=lambda _: ReviewDecision("approve"),
        )
    assert github.create_calls == 0


def test_changed_policy_prose_invalidates_an_old_approval(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    ledger_value.record_approval(attempt_id, approval)
    changed = candidate().model_copy(update={"title": "A newly authored title"})
    ledger_value.record_policy_result(
        attempt_id,
        {"schema_version": "1.0", "results": [changed.model_dump(mode="json")]},
    )

    with pytest.raises(ValueError, match="stale"):
        WriteCoordinator(ledger_value, FakeGitHub()).publish([approval], attempt_id=attempt_id)


def test_unverified_create_response_remains_uncertain_for_reconciliation(tmp_path: Path) -> None:
    ledger_value, attempt_id = ledger(tmp_path)
    approval = approved(ledger_value)
    wrong_destination = GitHubIssue(
        destination_repository="other/repository",
        number=20,
        title=approval.payload.title,
        body=approval.payload.body,
        html_url="https://github.com/other/repository/issues/20",
        state="open",
    )

    with pytest.raises(ExternalWriteFailure) as raised:
        WriteCoordinator(ledger_value, FakeGitHub(create_results=[wrong_destination])).publish(
            [approval], attempt_id=attempt_id
        )

    assert raised.value.code == "external_write_uncertain"
    assert [event["state"] for event in ledger_value.write_attempts] == [
        "write_pending",
        "write_uncertain",
    ]
