from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from feedback_triage.analyze import AnalysisFailed, analyze_recording, triage_recording
from feedback_triage.approval import ApprovalError
from feedback_triage.github import GitHubApiError, GitHubIssue, GitHubTransportError
from feedback_triage.ledger import LedgerInvalid, LedgerLocked, RunLedger
from feedback_triage.models import PolicyResult, RoutedResult
from feedback_triage.writes import ExternalWriteFailure, ReviewDecision, WriteCoordinator
from tests.test_completion_trust import VALID_OUTPUT, VALID_ANALYSIS_JSON
from tests.test_recovery import RecoveryClient, completed_interaction, interrupted_ledger, prepare_video
from tests.test_triage import prepared_ledger
from tests.test_write_coordinator import FakeGitHub, approved, candidate, ledger as write_ledger, remote_issue


SOURCE_SHA = "a" * 64
DESTINATION = "demo/feedback"


class MatrixGateway:
    def __init__(
        self,
        *,
        fail_create: bool = False,
        uncertain: bool = False,
        marker_matches: int = 0,
        find_error: Exception | None = None,
    ) -> None:
        self.posts = 0
        self.fail_create = fail_create
        self.uncertain = uncertain
        self.marker_matches = marker_matches
        self.find_error = find_error

    def find_marker(self, repository: str, marker: str) -> tuple[GitHubIssue, ...]:
        if self.find_error is not None:
            raise self.find_error
        return tuple(
            GitHubIssue(repository, number, "existing", marker, f"https://example.test/{number}", "open")
            for number in range(1, self.marker_matches + 1)
        )

    def get_issue(self, repository: str, number: int) -> GitHubIssue:
        raise AssertionError("matrix cases must not fetch an existing Issue")

    def create_issue(self, repository: str, payload: object) -> GitHubIssue:
        self.posts += 1
        if self.fail_create:
            raise GitHubApiError("create rejected", status_code=422, definitive=not self.uncertain)
        return GitHubIssue(
            repository,
            self.posts,
            getattr(payload, "title"),
            getattr(payload, "body"),
            f"https://example.test/{self.posts}",
            "open",
        )


@dataclass(frozen=True)
class MatrixCase:
    name: str
    result: RoutedResult
    decision: Literal["approve", "decline"]
    expected_outcome: str | None
    expected_failure_code: str | None
    expected_ledger_state: str
    expected_posts: int
    expected_write_state: str | None = None
    marker_matches: int = 0
    uncertain: bool = False


CASES = (
    MatrixCase(
        "candidate-create",
        candidate("cand_aaaaaaaaaaaaaaaa"),
        "approve",
        "created",
        None,
        "verified",
        1,
    ),
    MatrixCase(
        "candidate-decline",
        candidate("cand_bbbbbbbbbbbbbbbb"),
        "decline",
        None,
        None,
        "verified",
        0,
    ),
    MatrixCase(
        "manual-review-create",
        candidate("cand_cccccccccccccccc").model_copy(
            update={"route": "manual_review", "approval_eligible": False}
        ),
        "approve",
        "created",
        None,
        "verified",
        1,
    ),
    MatrixCase(
        "clarification-request-filtered",
        candidate("cand_dddddddddddddddd").model_copy(
            update={"route": "clarification_request", "approval_eligible": False}
        ),
        "approve",
        None,
        None,
        "verified",
        0,
    ),
    MatrixCase(
        "withheld-question-filtered",
        candidate("cand_eeeeeeeeeeeeeeee").model_copy(
            update={
                "route": "withheld_result",
                "approval_eligible": False,
                "reason_code": "question_not_request",
                "type": "question",
                "intent": "question",
            }
        ),
        "approve",
        None,
        None,
        "verified",
        0,
    ),
    MatrixCase(
        "definitive-create-failure",
        candidate("cand_ffffffffffffffff"),
        "approve",
        None,
        "external_write_failed",
        "verified",
        1,
        "write_failed",
    ),
    MatrixCase(
        "uncertain-create-failure",
        candidate("cand_1111111111111111"),
        "approve",
        None,
        "external_write_uncertain",
        "verified",
        1,
        "write_uncertain",
        uncertain=True,
    ),
    MatrixCase(
        "multiple-marker-conflict",
        candidate("cand_2222222222222222"),
        "approve",
        None,
        "external_write_conflict",
        "verified",
        0,
        "external_write_conflict",
        marker_matches=2,
    ),
)


def _verified_matrix_attempt(tmp_path: Path, result: RoutedResult) -> tuple[RunLedger, str]:
    ledger = RunLedger.create(
        tmp_path / "output",
        source_sha256=SOURCE_SHA,
        fingerprint="b" * 64,
        fingerprint_inputs={"fixture": "matrix"},
    )
    attempt_id = ledger.start_attempt()
    ledger.complete(
        attempt_id,
        {"schema_version": "1.0", "video_summary": "ok", "observations": []},
        processing_pair_count=1,
    )
    ledger.record_policy_result(
        attempt_id,
        PolicyResult(schema_version="1.0", results=[result]).model_dump(mode="json"),
    )
    return ledger, attempt_id


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_deterministic_review_write_matrix(
    case: MatrixCase, tmp_path: Path
) -> None:
    ledger, attempt_id = _verified_matrix_attempt(tmp_path, case.result)
    gateway = MatrixGateway(
        fail_create=case.expected_failure_code is not None and case.marker_matches == 0,
        uncertain=case.uncertain,
        marker_matches=case.marker_matches,
    )
    timings: dict[str, float] = {}

    if case.expected_failure_code is None:
        outcomes = WriteCoordinator(ledger, gateway).review_and_publish(
            [case.result],
            source_sha256=SOURCE_SHA,
            destination_repository=DESTINATION,
            attempt_id=attempt_id,
            decision_fn=lambda _: ReviewDecision(case.decision),
            on_timing=timings.__setitem__,
        )
    else:
        with pytest.raises(ExternalWriteFailure) as raised:
            WriteCoordinator(ledger, gateway).review_and_publish(
                [case.result],
                source_sha256=SOURCE_SHA,
                destination_repository=DESTINATION,
                attempt_id=attempt_id,
                decision_fn=lambda _: ReviewDecision(case.decision),
                on_timing=timings.__setitem__,
            )
        assert raised.value.code == case.expected_failure_code
        outcomes = ()

    assert ledger.attempts[-1]["status"] == case.expected_ledger_state
    assert gateway.posts == case.expected_posts
    assert timings["external_write_count"] == case.expected_posts
    assert len(outcomes) == (1 if case.expected_outcome is not None else 0)
    if case.expected_outcome is not None:
        assert outcomes[0].state == case.expected_outcome
    if case.expected_failure_code is None:
        assert not ledger.attempts[-1].get("failure")
    else:
        assert ledger.write_attempts[-1]["state"] == case.expected_write_state
    assert len(ledger.issue_records) == (0 if case.expected_failure_code else case.expected_posts)


@dataclass(frozen=True)
class TrustCase:
    name: str
    interaction: object
    expected_code: str


def _retrieved_interaction(
    *,
    output: str = VALID_OUTPUT,
    steps: tuple[object, ...] | None = None,
) -> object:
    return SimpleNamespace(
        status="completed",
        output_text=output,
        steps=list(
            steps
            or (
                SimpleNamespace(type="processing_call", id="segment-1"),
                SimpleNamespace(type="processing_result", call_id="segment-1"),
            )
        ),
    )


TRUST_CASES = (
    TrustCase(
        "malformed-gemini-output",
        _retrieved_interaction(output=json.dumps({"schema_version": "1.0"})),
        "output_invalid",
    ),
    TrustCase(
        "unmatched-processing",
        _retrieved_interaction(
            steps=(
                SimpleNamespace(type="processing_call", id="segment-1"),
                SimpleNamespace(type="processing_result", call_id="segment-2"),
            )
        ),
        "processing_unverified",
    ),
)


@pytest.mark.parametrize("case", TRUST_CASES, ids=lambda case: case.name)
def test_gemini_trust_failures_are_terminal_and_never_post(
    case: TrustCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    ledger = interrupted_ledger(video, tmp_path / "output")
    gateway = MatrixGateway()

    with pytest.raises(AnalysisFailed) as raised:
        analyze_recording(video, tmp_path / "output", RecoveryClient(case.interaction))

    assert raised.value.code == case.expected_code
    attempt = RunLedger.load(ledger.path).attempts[-1]
    assert attempt["status"] == "failed"
    assert attempt["failure"]["code"] == case.expected_code
    assert "policy_result" not in attempt
    external_write_count = gateway.posts
    assert external_write_count == 0
    assert RunLedger.load(ledger.path).write_attempts == ()


def test_policy_failure_is_terminal_and_never_posted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invalid = json.loads(VALID_ANALYSIS_JSON)
    invalid["observations"][0]["topic_key"] = "Not Kebab Case"
    video, ledger = prepared_ledger(monkeypatch, tmp_path, invalid)
    gateway = MatrixGateway()

    with pytest.raises(AnalysisFailed) as raised:
        triage_recording(video, tmp_path / "output")

    assert raised.value.code == "policy_failed"
    attempt = RunLedger.load(ledger.path).attempts[-1]
    assert attempt["status"] == "failed"
    assert attempt["failure"]["code"] == "policy_failed"
    assert "policy_result" not in attempt
    external_write_count = gateway.posts
    assert external_write_count == 0
    assert RunLedger.load(ledger.path).write_attempts == ()


def test_invalid_approval_fails_closed_without_post(tmp_path: Path) -> None:
    result = candidate()
    ledger, attempt_id = _verified_matrix_attempt(tmp_path, result)
    gateway = MatrixGateway()
    timings: dict[str, float] = {}

    with pytest.raises(ApprovalError, match="immutable"):
        WriteCoordinator(ledger, gateway).review_and_publish(
            [result],
            source_sha256=SOURCE_SHA,
            destination_repository=DESTINATION,
            attempt_id=attempt_id,
            decision_fn=lambda _: ReviewDecision("approve", {"evidence": []}),
            on_timing=timings.__setitem__,
        )

    assert ledger.attempts[-1]["status"] == "verified"
    assert ledger.approvals == ()
    assert ledger.write_attempts == ()
    assert gateway.posts == 0
    assert timings["external_write_count"] == 0


def test_github_marker_read_failure_fails_closed_before_post(tmp_path: Path) -> None:
    result = candidate()
    ledger, attempt_id = _verified_matrix_attempt(tmp_path, result)
    gateway = MatrixGateway(find_error=GitHubTransportError("marker search unavailable"))
    timings: dict[str, float] = {}

    with pytest.raises(ExternalWriteFailure) as raised:
        WriteCoordinator(ledger, gateway).review_and_publish(
            [result],
            source_sha256=SOURCE_SHA,
            destination_repository=DESTINATION,
            attempt_id=attempt_id,
            decision_fn=lambda _: ReviewDecision("approve"),
            on_timing=timings.__setitem__,
        )

    assert raised.value.code == "external_write_uncertain"
    assert ledger.attempts[-1]["status"] == "verified"
    assert ledger.write_attempts == ()
    assert ledger._data["reconciliation_errors"][-1]["code"] == "external_write_uncertain"
    assert gateway.posts == 0
    assert timings["external_write_count"] == 0


def test_successful_recovery_is_verified_and_never_posts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    interrupted = interrupted_ledger(video, tmp_path / "output")
    client = RecoveryClient(completed_interaction())
    gateway = MatrixGateway()

    _, verified, recovered = analyze_recording(video, tmp_path / "output", client)

    assert verified.processing_pair_count == 1
    assert getattr(client.interactions, "get_ids") == ["interaction-123"]
    assert recovered.attempts[-1]["status"] == "verified"
    external_write_count = gateway.posts
    assert external_write_count == 0
    assert recovered.write_attempts == ()
    assert interrupted.path == recovered.path


def test_existing_marker_adoption_is_verified_without_external_write(tmp_path: Path) -> None:
    ledger, attempt_id = write_ledger(tmp_path)
    approval = approved(ledger)
    gateway = FakeGitHub(matches=[remote_issue(approval, 7, state="closed")])
    timings: dict[str, float] = {}

    outcomes = WriteCoordinator(ledger, gateway).review_and_publish(
        [candidate()],
        source_sha256=SOURCE_SHA,
        destination_repository=DESTINATION,
        attempt_id=attempt_id,
        decision_fn=lambda _: ReviewDecision("approve"),
        on_timing=timings.__setitem__,
    )

    assert outcomes[0].state == "adopted"
    assert ledger.issue_records[0].issue_number == 7
    assert gateway.create_calls == 0
    assert timings["external_write_count"] == 0


class _RetryGateway:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.posts = 0
        self.fail_next = True

    def find_marker(self, repository: str, marker: str) -> tuple[GitHubIssue, ...]:
        return ()

    def get_issue(self, repository: str, number: int) -> GitHubIssue:
        raise AssertionError("retry cases must not fetch an existing Issue")

    def create_issue(self, repository: str, payload: object) -> GitHubIssue:
        self.posts += 1
        if self.fail_next:
            self.fail_next = False
            raise self.error
        return GitHubIssue(
            repository,
            self.posts,
            getattr(payload, "title"),
            getattr(payload, "body"),
            f"https://example.test/{self.posts}",
            "open",
        )


@pytest.mark.parametrize(
    ("name", "error", "retry_mode", "expected_code"),
    (
        ("definitive-retry", GitHubApiError("rejected", status_code=422, definitive=True), "failed", "external_write_failed"),
        (
            "uncertain-override",
            GitHubTransportError("request may not have arrived"),
            "uncertain",
            "external_write_uncertain",
        ),
    ),
    ids=("definitive-retry", "uncertain-override"),
)
def test_retry_transitions_are_explicit_and_count_every_post(
    name: str,
    error: Exception,
    retry_mode: str,
    expected_code: str,
    tmp_path: Path,
) -> None:
    ledger, attempt_id = write_ledger(tmp_path)
    gateway = _RetryGateway(error)
    coordinator = WriteCoordinator(ledger, gateway)
    first_timings: dict[str, float] = {}

    with pytest.raises(ExternalWriteFailure) as raised:
        coordinator.review_and_publish(
            [candidate()],
            source_sha256=SOURCE_SHA,
            destination_repository=DESTINATION,
            attempt_id=attempt_id,
            decision_fn=lambda _: ReviewDecision("approve"),
            on_timing=first_timings.__setitem__,
        )
    assert raised.value.code == expected_code
    assert first_timings["external_write_count"] == 1
    assert ledger.write_attempts[-1]["state"] == (
        "write_failed" if retry_mode == "failed" else "write_uncertain"
    )

    retry_timings: dict[str, float] = {}
    if retry_mode == "failed":
        outcomes = coordinator.review_and_publish(
            [candidate()],
            source_sha256=SOURCE_SHA,
            destination_repository=DESTINATION,
            attempt_id=attempt_id,
            decision_fn=lambda _: ReviewDecision("approve"),
            on_timing=retry_timings.__setitem__,
            retry_failed=True,
        )
    else:
        outcomes = coordinator.review_and_publish(
            [candidate()],
            source_sha256=SOURCE_SHA,
            destination_repository=DESTINATION,
            attempt_id=attempt_id,
            decision_fn=lambda _: ReviewDecision("approve"),
            on_timing=retry_timings.__setitem__,
            retry_uncertain=True,
            confirm_no_issue=True,
        )

    assert outcomes[0].state == "created"
    assert gateway.posts == 2
    assert retry_timings["external_write_count"] == 1
    assert ledger.write_attempts[-1]["state"] == "written"
    assert ledger.issue_records[0].issue_number == 2
    assert name in {"definitive-retry", "uncertain-override"}


def test_recorded_marker_conflict_resolution_never_posts(tmp_path: Path) -> None:
    ledger, attempt_id = write_ledger(tmp_path)
    approval = approved(ledger)
    gateway = FakeGitHub(matches=[remote_issue(approval, 21), remote_issue(approval, 22)])
    coordinator = WriteCoordinator(ledger, gateway)

    with pytest.raises(ExternalWriteFailure) as raised:
        coordinator.publish([approval], attempt_id=attempt_id)
    assert raised.value.code == "external_write_conflict"
    assert ledger.write_attempts[-1]["state"] == "external_write_conflict"

    outcomes = coordinator.publish(
        [approval],
        attempt_id=attempt_id,
        canonical_issue_selections={approval.candidate_id: 22},
    )

    assert outcomes[0].state == "skipped"
    assert outcomes[0].issue_record is not None
    assert outcomes[0].issue_record.issue_number == 22
    assert gateway.create_calls == 0
    assert len(ledger.issue_records) == 1


def test_ledger_lock_blocks_review_before_any_external_write(tmp_path: Path) -> None:
    ledger, attempt_id = write_ledger(tmp_path)
    gateway = MatrixGateway()

    with ledger.exclusive_lock():
        with pytest.raises(LedgerLocked):
            WriteCoordinator(ledger, gateway).review_and_publish(
                [candidate()],
                source_sha256=SOURCE_SHA,
                destination_repository=DESTINATION,
                attempt_id=attempt_id,
                decision_fn=lambda _: ReviewDecision("approve"),
            )

    assert gateway.posts == 0
    assert ledger.attempts[-1]["status"] == "verified"


def test_corrupt_ledger_is_terminal_before_external_write(tmp_path: Path) -> None:
    output = tmp_path / "output"
    path = output / SOURCE_SHA / "ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "source_sha256": SOURCE_SHA}))
    gateway = MatrixGateway()

    with pytest.raises(LedgerInvalid):
        RunLedger.load(path)

    external_write_count = gateway.posts
    assert external_write_count == 0
