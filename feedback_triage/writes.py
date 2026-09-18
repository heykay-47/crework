"""The fail-closed review-to-GitHub write coordinator."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import ValidationError

from .approval import EDITABLE_FIELDS, build_approval, marker_for, render_issue_payload
from .github import GitHubApiError, GitHubIssue
from .ledger import LedgerInvalid, RunLedger
from .models import (
    Approval,
    IssuePayload,
    IssueRecord,
    PolicyResult,
    RoutedResult,
    issue_payload_hash,
    routed_result_hash,
)
from .timing import report_timing


class IssueGateway(Protocol):
    def find_marker(self, destination_repository: str, marker: str) -> tuple[GitHubIssue, ...]: ...

    def get_issue(self, destination_repository: str, issue_number: int) -> GitHubIssue: ...

    def create_issue(self, destination_repository: str, payload: IssuePayload) -> GitHubIssue: ...


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    candidate_id: str
    state: Literal["created", "adopted", "skipped"]
    issue_record: IssueRecord


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    action: Literal["approve", "decline"]
    changes: Mapping[str, object] = field(default_factory=dict)


class ExternalWriteFailure(RuntimeError):
    """A write stopped with an explicit, machine-readable next action."""

    def __init__(
        self,
        code: Literal["external_write_failed", "external_write_uncertain", "external_write_conflict"],
        *,
        candidate_id: str,
        destination_repository: str,
        candidate_snapshot_hash: str | None = None,
        payload_hash: str,
        detail: str,
        stage: str = "reconcile",
        remote_status: int | None = None,
        remote_error: str | None = None,
        next_action: str | None = None,
    ) -> None:
        self.code = code
        self.candidate_id = candidate_id
        self.destination_repository = destination_repository
        self.candidate_snapshot_hash = candidate_snapshot_hash
        self.payload_hash = payload_hash
        self.detail = detail
        self.stage = stage
        self.remote_status = remote_status
        self.remote_error = remote_error
        self.next_action = next_action or _default_next_action(code)
        super().__init__(
            f"{code}: Candidate {candidate_id} in {destination_repository}: {detail}"
        )


def _default_next_action(
    code: Literal["external_write_failed", "external_write_uncertain", "external_write_conflict"],
) -> str:
    if code == "external_write_failed":
        return "Review the definitive rejection and explicitly retry with --retry-failed if appropriate."
    if code == "external_write_uncertain":
        return "Reconcile the exact marker; use --retry-uncertain --confirm-no-issue only after human certification."
    return "Resolve the remote or ledger conflict explicitly; never recreate or mutate the recorded Issue."


def terminal_review_decision(
    candidate: RoutedResult,
    *,
    prompt: Callable[[str], str] = input,
    emit: Callable[[str], None] = print,
    evidence_frame_paths: Sequence[str] = (),
) -> ReviewDecision:
    """Ask for one explicit terminal decision without exposing immutable fields."""

    emit(f"\nCandidate {candidate.candidate_id}: {candidate.title}")
    emit(candidate.summary)
    if candidate.route == "manual_review":
        if evidence_frame_paths:
            for path in evidence_frame_paths:
                emit(f"Evidence Frame: {path}")
        else:
            emit("Evidence Frame: unavailable; visual confirmation is still required.")
        choice = prompt("Manual Review: [c]onfirm, [e]dit prose, or [d]ecline? ").strip().lower()
        if choice in {"c", "confirm", "y", "yes"}:
            return ReviewDecision("approve")
        if choice in {"e", "edit"}:
            changes: dict[str, object] = {}
            for field_name in ("title", "summary", "requested_outcome", "component"):
                current = getattr(candidate, field_name) or ""
                value = prompt(f"{field_name} [{current}] (blank keeps it): ").strip()
                if value:
                    changes[field_name] = value
            criteria = prompt(
                "acceptance_criteria (semicolon-separated; blank keeps it): "
            ).strip()
            if criteria:
                changes["acceptance_criteria"] = [item.strip() for item in criteria.split(";") if item.strip()]
            return ReviewDecision("approve", changes)
        return ReviewDecision("decline")
    choice = prompt("Approve this Candidate? [y/N] ").strip().lower()
    return ReviewDecision("approve" if choice in {"y", "yes", "a", "approve"} else "decline")


class WriteCoordinator:
    """Persist approval/write transitions and serialize safe Issue creation."""

    def __init__(self, ledger: RunLedger, github: IssueGateway) -> None:
        self.ledger = ledger
        self.github = github

    def publish(
        self,
        approvals: list[Approval] | tuple[Approval, ...],
        *,
        attempt_id: str,
        retry_failed: bool = False,
        retry_uncertain: bool = False,
        confirm_no_issue: bool = False,
        canonical_issue_selections: Mapping[str, int] | None = None,
    ) -> tuple[PublishOutcome, ...]:
        """Reconcile, persist approvals, and write sequentially under one lock."""

        with self.ledger.exclusive_lock():
            retry_ids = self._reconcile_locked(
                attempt_id=attempt_id,
                retry_uncertain=retry_uncertain,
                confirm_no_issue=confirm_no_issue,
                canonical_issue_selections=canonical_issue_selections or {},
            )
            self._validate_approvals(approvals, attempt_id)
            for approval in approvals:
                self.ledger.record_approval(attempt_id, approval)
            return self._publish_locked(
                tuple(approvals),
                attempt_id=attempt_id,
                retry_failed=retry_failed,
                retry_ids=retry_ids,
                canonical_issue_selections=canonical_issue_selections or {},
            )

    def review_and_publish(
        self,
        candidates: list[RoutedResult] | tuple[RoutedResult, ...],
        *,
        source_sha256: str,
        destination_repository: str,
        attempt_id: str,
        decision_fn: Callable[[RoutedResult], ReviewDecision],
        operator_label: str | None = None,
        reconsider: bool = False,
        retry_failed: bool = False,
        retry_uncertain: bool = False,
        confirm_no_issue: bool = False,
        canonical_issue_selections: Mapping[str, int] | None = None,
        on_timing: Callable[[str, float], None] | None = None,
        require_fresh_review: bool = False,
    ) -> tuple[PublishOutcome, ...]:
        """Review only admitted routes, persist decisions, then write sequentially."""

        active_human_seconds = 0.0
        active_review_started: float | None = None
        active_review_seconds: float | None = None
        write_started: float | None = None
        external_write_count = 0

        def count_external_write() -> None:
            nonlocal external_write_count
            external_write_count += 1

        try:
            with self.ledger.exclusive_lock():
                persisted_candidates = self._policy_candidates(attempt_id, source_sha256)
                retry_ids = self._reconcile_locked(
                    attempt_id=attempt_id,
                    retry_uncertain=retry_uncertain,
                    confirm_no_issue=confirm_no_issue,
                    canonical_issue_selections=canonical_issue_selections or {},
                )
                approvals: list[Approval] = []
                reviewed_approvals: list[Approval] = []
                outcomes: list[PublishOutcome] = []
                active_review_started = time.monotonic()
                for candidate in candidates:
                    persisted = persisted_candidates.get(candidate.candidate_id)
                    if persisted is None or persisted.model_dump(mode="json") != candidate.model_dump(mode="json"):
                        raise ValueError("review input must match a Candidate in the persisted policy result")
                    if candidate.route == "candidate" and not candidate.approval_eligible:
                        continue
                    if candidate.route not in {"candidate", "manual_review"}:
                        continue
                    default_approval = build_approval(
                        source_sha256,
                        candidate,
                        destination_repository,
                        manual_review_confirmed=candidate.route == "manual_review",
                        operator_label=operator_label,
                    )
                    prior_approval = self.ledger.latest_approval_for(
                        candidate_id=default_approval.candidate_id,
                        destination_repository=default_approval.destination_repository,
                    )
                    if (
                        not require_fresh_review
                        and prior_approval is not None
                        and prior_approval.candidate_snapshot_hash == default_approval.candidate_snapshot_hash
                        and prior_approval.candidate_payload_hash == default_approval.candidate_payload_hash
                    ):
                        reviewed_approvals.append(prior_approval)
                        existing = self._record_for_identity(prior_approval)
                        if existing is not None:
                            outcomes.append(PublishOutcome(candidate.candidate_id, "skipped", existing))
                        else:
                            approvals.append(prior_approval)
                        continue
                    declined = self.ledger.has_decline(
                        source_sha256=source_sha256,
                        candidate_id=default_approval.candidate_id,
                        destination_repository=default_approval.destination_repository,
                        candidate_snapshot_hash=default_approval.candidate_snapshot_hash,
                        payload_hash=default_approval.payload_hash,
                    )
                    if declined and not reconsider and not require_fresh_review:
                        continue
                    if reconsider and declined:
                        self.ledger.record_reconsideration(
                            attempt_id,
                            source_sha256=source_sha256,
                            candidate_id=default_approval.candidate_id,
                            destination_repository=default_approval.destination_repository,
                            candidate_snapshot_hash=default_approval.candidate_snapshot_hash,
                            payload_hash=default_approval.payload_hash,
                        )
                    decision_started = time.monotonic()
                    try:
                        decision = decision_fn(candidate)
                    finally:
                        active_human_seconds += time.monotonic() - decision_started
                    if decision.action not in {"approve", "decline"}:
                        raise ValueError("review decision must be approve or decline")
                    if decision.action == "decline":
                        self.ledger.record_decline(
                            attempt_id,
                            source_sha256=source_sha256,
                            candidate_id=default_approval.candidate_id,
                            destination_repository=default_approval.destination_repository,
                            candidate_snapshot_hash=default_approval.candidate_snapshot_hash,
                            payload_hash=default_approval.payload_hash,
                        )
                        continue
                    approval = build_approval(
                        source_sha256,
                        candidate,
                        destination_repository,
                        changes=decision.changes or None,
                        manual_review_confirmed=candidate.route == "manual_review",
                        operator_label=operator_label,
                    )
                    self.ledger.record_approval(attempt_id, approval)
                    reviewed_approvals.append(approval)
                    existing = self._record_for_identity(approval)
                    if existing is not None:
                        outcomes.append(PublishOutcome(candidate.candidate_id, "skipped", existing))
                    else:
                        approvals.append(approval)
                self._validate_approvals(tuple(reviewed_approvals), attempt_id)
                self._validate_approvals(tuple(approvals), attempt_id)
                if active_review_started is not None:
                    active_review_seconds = time.monotonic() - active_review_started
                write_started = time.monotonic()
                outcomes.extend(
                    self._publish_locked(
                        tuple(approvals),
                        attempt_id=attempt_id,
                        retry_failed=retry_failed,
                        retry_ids=retry_ids,
                        canonical_issue_selections=canonical_issue_selections or {},
                        on_external_write=count_external_write,
                    )
                )
                return tuple(outcomes)
        finally:
            report_timing(on_timing, "active_human_seconds", active_human_seconds)
            if active_review_seconds is None and active_review_started is not None:
                active_review_seconds = time.monotonic() - active_review_started
            if active_review_seconds is not None:
                report_timing(on_timing, "active_review_seconds", active_review_seconds)
            report_timing(on_timing, "external_write_count", float(external_write_count))
            if write_started is not None:
                report_timing(on_timing, "write_seconds", time.monotonic() - write_started)

    def _policy_candidates(self, attempt_id: str, source_sha256: str) -> dict[str, RoutedResult]:
        if self.ledger.source_sha256 != source_sha256:
            raise ValueError("source identity does not match the Run Ledger")
        for attempt in self.ledger.attempts:
            if attempt.get("attempt_id") != attempt_id:
                continue
            if attempt.get("status") != "verified":
                raise ValueError("review requires a verified analysis attempt")
            policy_data = attempt.get("policy_result")
            if not isinstance(policy_data, dict):
                raise ValueError("review requires a persisted policy result")
            policy = PolicyResult.model_validate(policy_data)
            candidates: dict[str, RoutedResult] = {}
            for candidate in policy.results:
                if candidate.candidate_id in candidates:
                    raise ValueError("persisted policy contains duplicate Candidate identities")
                candidates[candidate.candidate_id] = candidate
            return candidates
        raise ValueError(f"unknown analysis attempt: {attempt_id}")

    def _validate_approvals(
        self,
        approvals: list[Approval] | tuple[Approval, ...],
        attempt_id: str,
    ) -> None:
        persisted_candidates = self._policy_candidates(attempt_id, self.ledger.source_sha256)
        for approval in approvals:
            candidate = persisted_candidates.get(approval.candidate_id)
            if candidate is None:
                raise ValueError("Approval Candidate is not present in the persisted policy result")
            if approval.candidate_snapshot_hash != routed_result_hash(candidate):
                raise ValueError("Approval is stale because the Candidate snapshot changed")
            baseline_payload = render_issue_payload(candidate, self.ledger.source_sha256)
            if approval.candidate_payload_hash != issue_payload_hash(baseline_payload):
                raise ValueError("Approval is stale because the reviewed Candidate prose changed")
            if self._immutable_candidate_data(approval.candidate_snapshot) != self._immutable_candidate_data(candidate):
                raise ValueError("Approval contains edited immutable Candidate evidence or provenance")
            if approval.payload != render_issue_payload(
                approval.candidate_snapshot, self.ledger.source_sha256
            ):
                raise ValueError("Approval payload does not match its reviewed Candidate snapshot")

    @staticmethod
    def _immutable_candidate_data(candidate: RoutedResult) -> dict[str, object]:
        data = candidate.model_dump(mode="json")
        for field_name in EDITABLE_FIELDS:
            data.pop(field_name, None)
        return data

    def _record_for_identity(self, approval: Approval) -> IssueRecord | None:
        for record in self.ledger.issue_records:
            if (
                record.source_sha256 == approval.source_sha256
                and record.candidate_id == approval.candidate_id
                and record.destination_repository == approval.destination_repository
            ):
                return record
        return None

    def _publish_locked(
        self,
        approvals: tuple[Approval, ...],
        *,
        attempt_id: str,
        retry_failed: bool,
        retry_ids: set[str],
        canonical_issue_selections: Mapping[str, int] | None = None,
        on_external_write: Callable[[], None] | None = None,
    ) -> tuple[PublishOutcome, ...]:
        outcomes: list[PublishOutcome] = []
        selections = canonical_issue_selections or {}
        for approval in approvals:
            existing = self.ledger.issue_record_for(approval)
            if existing is not None:
                outcomes.append(PublishOutcome(approval.candidate_id, "skipped", existing))
                continue

            prior = self._latest_for(approval)
            if prior is not None and prior.get("state") == "external_write_conflict":
                raise self._failure(
                    "external_write_conflict",
                    approval,
                    "a prior remote or ledger conflict requires human resolution",
                )

            marker = marker_for(approval.source_sha256, approval.candidate_id)
            matches = self._find_matches(approval, marker)
            if len(matches) > 1:
                write_id = str(uuid4())
                try:
                    selected = self._selected_match(approval, matches, selections)
                except ExternalWriteFailure as error:
                    self.ledger.record_write_state(
                        write_id,
                        attempt_id,
                        approval,
                        "external_write_conflict",
                        detail=error.detail,
                        stage=error.stage,
                        remote_status=error.remote_status,
                        remote_error=error.remote_error,
                        next_action=error.next_action,
                    )
                    raise
                if selected is not None:
                    try:
                        record = self._adopt_selected_match(
                            approval,
                            selected,
                            matches,
                            attempt_id=attempt_id,
                            write_id=write_id,
                        )
                    except ExternalWriteFailure as error:
                        self.ledger.record_write_state(
                            write_id,
                            attempt_id,
                            approval,
                            "external_write_conflict",
                            detail=error.detail,
                            stage=error.stage,
                            remote_status=error.remote_status,
                            remote_error=error.remote_error,
                            next_action=error.next_action,
                        )
                        raise
                    outcomes.append(PublishOutcome(approval.candidate_id, "adopted", record))
                    continue
                failure = self._failure(
                    "external_write_conflict",
                    approval,
                    self._matches_detail("found multiple exact marker matches", matches),
                )
                self.ledger.record_write_state(
                    write_id,
                    attempt_id,
                    approval,
                    "external_write_conflict",
                    detail=failure.detail,
                    stage=failure.stage,
                    remote_status=failure.remote_status,
                    remote_error=failure.remote_error,
                    next_action=failure.next_action,
                )
                raise failure
            if len(matches) == 1:
                try:
                    record = self._record_from_remote(approval, matches[0])
                except ExternalWriteFailure as error:
                    self.ledger.record_write_state(
                        str(uuid4()),
                        attempt_id,
                        approval,
                        "external_write_conflict",
                        detail=error.detail,
                        stage=error.stage,
                        remote_status=error.remote_status,
                        remote_error=error.remote_error,
                        next_action=error.next_action,
                    )
                    raise
                self.ledger.record_write_state(
                    str(uuid4()),
                    attempt_id,
                    approval,
                    "written",
                    detail="adopted an existing exact-marker Issue without POST",
                    issue_record=record,
                )
                outcomes.append(PublishOutcome(approval.candidate_id, "adopted", record))
                continue

            if prior is not None and prior.get("state") in {"write_pending", "write_uncertain"}:
                prior_write_id = prior.get("write_id")
                if not isinstance(prior_write_id, str) or prior_write_id not in retry_ids:
                    raise self._failure(
                        "external_write_uncertain",
                        approval,
                        "an earlier write remains unresolved; reconcile it before retrying",
                    )
            elif prior is not None and prior.get("state") == "write_failed" and not retry_failed:
                raise self._failure(
                    "external_write_failed",
                    approval,
                    "a definitive rejection is recorded; explicit retry is required",
                )

            if prior is not None and prior.get("state") == "write_failed" and retry_failed:
                self.ledger.record_override(
                    attempt_id,
                    approval,
                    write_id=str(prior.get("write_id", "")),
                    detail="human explicitly authorized retry after definitive rejection",
                )
            write_id = self.ledger.record_write_pending(attempt_id, approval)
            try:
                if on_external_write is not None:
                    on_external_write()
                remote = self.github.create_issue(approval.destination_repository, approval.payload)
            except GitHubApiError as error:
                state = "write_failed" if error.definitive else "write_uncertain"
                code: Literal["external_write_failed", "external_write_uncertain"] = (
                    "external_write_failed" if error.definitive else "external_write_uncertain"
                )
                failure = self._failure(
                    code,
                    approval,
                    str(error),
                    stage="create",
                    remote_status=error.status_code,
                    remote_error=str(error),
                )
                self.ledger.record_write_state(
                    write_id,
                    attempt_id,
                    approval,
                    state,
                    detail=failure.detail,
                    stage=failure.stage,
                    remote_status=failure.remote_status,
                    remote_error=failure.remote_error,
                    next_action=failure.next_action,
                )
                raise failure from error
            except Exception as error:
                failure = self._failure(
                    "external_write_uncertain",
                    approval,
                    f"unclassified create error: {error}",
                    stage="create",
                    remote_error=str(error),
                )
                self.ledger.record_write_state(
                    write_id,
                    attempt_id,
                    approval,
                    "write_uncertain",
                    detail=failure.detail,
                    stage=failure.stage,
                    remote_status=failure.remote_status,
                    remote_error=failure.remote_error,
                    next_action=failure.next_action,
                )
                raise failure from error

            try:
                record = self._record_from_remote(approval, remote)
            except ExternalWriteFailure as error:
                failure = self._failure(
                    "external_write_uncertain",
                    approval,
                    f"create response could not be verified: {error.detail}",
                    stage="verify",
                    remote_error=error.detail,
                )
                self.ledger.record_write_state(
                    write_id,
                    attempt_id,
                    approval,
                    "write_uncertain",
                    detail=failure.detail,
                    stage=failure.stage,
                    remote_status=failure.remote_status,
                    remote_error=failure.remote_error,
                    next_action=failure.next_action,
                )
                raise failure from error
            self.ledger.record_write_state(
                write_id,
                attempt_id,
                approval,
                "written",
                issue_record=record,
            )
            outcomes.append(PublishOutcome(approval.candidate_id, "created", record))
        return tuple(outcomes)

    def _reconcile_locked(
        self,
        *,
        attempt_id: str,
        retry_uncertain: bool,
        confirm_no_issue: bool,
        canonical_issue_selections: Mapping[str, int],
    ) -> set[str]:
        retry_ids: set[str] = set()
        for record in self.ledger.issue_records:
            try:
                remote = self.github.get_issue(record.destination_repository, record.issue_number)
            except Exception as error:
                failure = self._record_failure_from_record(
                    record,
                    "external_write_conflict" if getattr(error, "status_code", None) == 404 else "external_write_uncertain",
                    f"could not verify recorded Issue: {error}",
                    candidate_snapshot_hash=self.ledger.snapshot_hash_for_record(record),
                    remote_status=getattr(error, "status_code", None),
                    remote_error=str(error),
                )
                self.ledger.record_reconciliation_error(
                    source_sha256=record.source_sha256,
                    candidate_id=record.candidate_id,
                    destination_repository=record.destination_repository,
                    code=failure.code,
                    detail=failure.detail,
                    candidate_snapshot_hash=failure.candidate_snapshot_hash,
                    stage=failure.stage,
                    remote_status=failure.remote_status,
                    remote_error=failure.remote_error,
                    next_action=failure.next_action,
                )
                raise failure from error
            if (
                not isinstance(remote.destination_repository, str)
                or not isinstance(remote.number, int)
                or isinstance(remote.number, bool)
                or not isinstance(remote.body, str)
                or not isinstance(remote.html_url, str)
                or remote.destination_repository.casefold() != record.destination_repository.casefold()
                or remote.number != record.issue_number
                or remote.html_url != record.html_url
                or not self._has_single_marker(remote.body, record.marker)
            ):
                failure = self._record_failure_from_record(
                    record,
                    "external_write_conflict",
                    "recorded Issue is missing, damaged, or has a mismatched marker",
                    candidate_snapshot_hash=self.ledger.snapshot_hash_for_record(record),
                )
                self.ledger.record_reconciliation_error(
                    source_sha256=record.source_sha256,
                    candidate_id=record.candidate_id,
                    destination_repository=record.destination_repository,
                    code=failure.code,
                    detail=failure.detail,
                    candidate_snapshot_hash=failure.candidate_snapshot_hash,
                    stage=failure.stage,
                    remote_status=failure.remote_status,
                    remote_error=failure.remote_error,
                    next_action=failure.next_action,
                )
                raise failure

        for event in self.ledger.latest_write_attempts():
            state = event.get("state")
            if state == "written":
                approval = self._approval_for_event(event)
                if approval is None:
                    raise ExternalWriteFailure(
                        "external_write_conflict",
                        candidate_id=str(event.get("candidate_id", "unknown")),
                        destination_repository=str(event.get("destination_repository", "unknown")),
                        candidate_snapshot_hash=str(event.get("candidate_snapshot_hash", "unknown")),
                        payload_hash=str(event.get("payload_hash", "unknown")),
                        detail="written event has no matching Approval snapshot",
                    )
                try:
                    matched_record = self.ledger.issue_record_for(approval)
                except LedgerInvalid as error:
                    raise self._failure(
                        "external_write_conflict", approval, "Run Ledger contains conflicting Issue Records"
                    ) from error
                event_record = event.get("issue_record")
                try:
                    persisted_event_record = IssueRecord.model_validate(event_record)
                except (TypeError, ValidationError) as error:
                    raise self._failure(
                        "external_write_conflict", approval, "written event has an invalid Issue Record"
                    ) from error
                if matched_record is None or not self._same_issue_record(
                    persisted_event_record, matched_record
                ):
                    self.ledger.record_reconciliation_error(
                        source_sha256=approval.source_sha256,
                        candidate_id=approval.candidate_id,
                        destination_repository=approval.destination_repository,
                        code="external_write_conflict",
                        detail="written event and destination-scoped Issue Record disagree or the record is missing",
                        candidate_snapshot_hash=approval.candidate_snapshot_hash,
                        stage="reconcile",
                        next_action="Resolve the ledger conflict explicitly; never recreate the recorded Issue.",
                    )
                    raise self._failure(
                        "external_write_conflict",
                        approval,
                        "written event and destination-scoped Issue Record disagree or the record is missing",
                    )
                continue
            if state == "external_write_conflict":
                approval = self._approval_for_event(event)
                if approval is not None:
                    write_id = event.get("write_id")
                    if isinstance(write_id, str):
                        resolution = self.ledger.conflict_resolution_for(write_id)
                        explicit_selection = canonical_issue_selections.get(approval.candidate_id)
                        resolution_matches = (
                            resolution is not None
                            and resolution.get("source_sha256") == approval.source_sha256
                            and resolution.get("candidate_id") == approval.candidate_id
                            and resolution.get("destination_repository") == approval.destination_repository
                            and resolution.get("candidate_snapshot_hash") == approval.candidate_snapshot_hash
                            and resolution.get("payload_hash") == approval.payload_hash
                        )
                        selected_number = explicit_selection
                        if selected_number is None and resolution_matches and resolution is not None:
                            selected_number = resolution.get("selected_issue_number")
                        if selected_number is not None:
                            matches = self._find_matches(
                                approval,
                                marker_for(approval.source_sha256, approval.candidate_id),
                            )
                            selected = self._selected_match(
                                approval,
                                matches,
                                {approval.candidate_id: selected_number},
                            )
                            if selected is not None:
                                prior_selected_number = (
                                    resolution.get("selected_issue_number")
                                    if resolution_matches and resolution is not None
                                    else None
                                )
                                self._adopt_selected_match(
                                    approval,
                                    selected,
                                    matches,
                                    attempt_id=attempt_id,
                                    write_id=write_id,
                                    record_resolution=(
                                        not resolution_matches
                                        or explicit_selection != prior_selected_number
                                    ),
                                )
                                continue
                    raise self._failure(
                        "external_write_conflict",
                        approval,
                        "a prior write conflict requires explicit human resolution",
                    )
                raise ExternalWriteFailure(
                    "external_write_conflict",
                    candidate_id=str(event.get("candidate_id", "unknown")),
                    destination_repository=str(event.get("destination_repository", "unknown")),
                    candidate_snapshot_hash=str(event.get("candidate_snapshot_hash", "unknown")),
                    payload_hash=str(event.get("payload_hash", "unknown")),
                    detail="a prior write conflict has no matching Approval snapshot",
                )
            if state not in {"write_pending", "write_uncertain"}:
                continue
            approval = self._approval_for_event(event)
            if approval is None:
                raise ExternalWriteFailure(
                    "external_write_conflict",
                    candidate_id=str(event.get("candidate_id", "unknown")),
                    destination_repository=str(event.get("destination_repository", "unknown")),
                    candidate_snapshot_hash=str(event.get("candidate_snapshot_hash", "unknown")),
                    payload_hash=str(event.get("payload_hash", "unknown")),
                    detail="write event has no matching Approval snapshot",
                )
            marker = marker_for(approval.source_sha256, approval.candidate_id)
            write_id = event.get("write_id")
            if not isinstance(write_id, str):
                raise self._failure("external_write_conflict", approval, "write event has no ID")
            existing_record = self.ledger.issue_record_for(approval)
            if existing_record is not None:
                self.ledger.record_write_state(
                    write_id,
                    str(event.get("attempt_id", "")),
                    approval,
                    "written",
                    detail="reconciled an earlier write to an existing verified Issue Record",
                    issue_record=existing_record,
                )
                continue
            try:
                matches = self._find_matches(approval, marker)
            except ExternalWriteFailure as error:
                self.ledger.record_write_state(
                    write_id,
                    str(event.get("attempt_id", "")),
                    approval,
                    "write_uncertain",
                    detail=error.detail,
                    stage=error.stage,
                    remote_status=error.remote_status,
                    remote_error=error.remote_error,
                    next_action=error.next_action,
                )
                raise
            if len(matches) == 1:
                try:
                    record = self._record_from_remote(approval, matches[0])
                except ExternalWriteFailure as error:
                    self.ledger.record_write_state(
                        write_id,
                        str(event.get("attempt_id", "")),
                        approval,
                        "external_write_conflict",
                        detail=error.detail,
                        stage=error.stage,
                        remote_status=error.remote_status,
                        remote_error=error.remote_error,
                        next_action=error.next_action,
                    )
                    raise
                self.ledger.record_write_state(
                    write_id,
                    str(event.get("attempt_id", "")),
                    approval,
                    "written",
                    detail="reconciled an earlier uncertain or pending write",
                    issue_record=record,
                )
                continue
            if len(matches) > 1:
                try:
                    selected = self._selected_match(approval, matches, canonical_issue_selections)
                except ExternalWriteFailure as error:
                    self.ledger.record_write_state(
                        write_id,
                        str(event.get("attempt_id", "")),
                        approval,
                        "external_write_conflict",
                        detail=error.detail,
                        stage=error.stage,
                        remote_status=error.remote_status,
                        remote_error=error.remote_error,
                        next_action=error.next_action,
                    )
                    raise
                if selected is not None:
                    try:
                        self._adopt_selected_match(
                            approval,
                            selected,
                            matches,
                            attempt_id=attempt_id,
                            write_id=write_id,
                        )
                    except ExternalWriteFailure as error:
                        self.ledger.record_write_state(
                            write_id,
                            str(event.get("attempt_id", "")),
                            approval,
                            "external_write_conflict",
                            detail=error.detail,
                            stage=error.stage,
                            remote_status=error.remote_status,
                            remote_error=error.remote_error,
                            next_action=error.next_action,
                        )
                        raise
                    continue
                failure = self._failure(
                    "external_write_conflict",
                    approval,
                    self._matches_detail(
                        "reconciliation found multiple exact marker matches", matches
                    ),
                )
                self.ledger.record_write_state(
                    write_id,
                    str(event.get("attempt_id", "")),
                    approval,
                    "external_write_conflict",
                    detail=failure.detail,
                    stage=failure.stage,
                    remote_status=failure.remote_status,
                    remote_error=failure.remote_error,
                    next_action=failure.next_action,
                )
                raise failure
            if retry_uncertain and confirm_no_issue:
                self.ledger.record_override(
                    attempt_id,
                    approval,
                    write_id=write_id,
                    detail="human certified no exact marker was found and explicitly authorized retry",
                )
                retry_ids.add(write_id)
                continue
            failure = self._failure(
                "external_write_uncertain",
                approval,
                "zero marker matches do not prove that creation did not occur",
            )
            self.ledger.record_write_state(
                write_id,
                str(event.get("attempt_id", "")),
                approval,
                "write_uncertain",
                detail=failure.detail,
                stage=failure.stage,
                remote_status=failure.remote_status,
                remote_error=failure.remote_error,
                next_action=failure.next_action,
            )
            raise failure
        return retry_ids

    def _find_matches(self, approval: Approval, marker: str) -> tuple[GitHubIssue, ...]:
        try:
            return self.github.find_marker(approval.destination_repository, marker)
        except Exception as error:
            failure = self._failure(
                "external_write_uncertain",
                approval,
                f"exact-marker search failed: {error}",
                stage="reconcile",
                remote_error=str(error),
            )
            self.ledger.record_reconciliation_error(
                source_sha256=approval.source_sha256,
                candidate_id=approval.candidate_id,
                destination_repository=approval.destination_repository,
                code=failure.code,
                detail=failure.detail,
                candidate_snapshot_hash=failure.candidate_snapshot_hash,
                stage=failure.stage,
                remote_status=failure.remote_status,
                remote_error=failure.remote_error,
                next_action=failure.next_action,
            )
            raise failure from error

    @staticmethod
    def _matches_detail(prefix: str, matches: tuple[GitHubIssue, ...]) -> str:
        identities = ", ".join(
            f"{match.destination_repository}#{match.number} ({match.html_url})" for match in matches
        )
        return f"{prefix}: {identities}"

    def _selected_match(
        self,
        approval: Approval,
        matches: tuple[GitHubIssue, ...],
        selections: Mapping[str, int],
    ) -> GitHubIssue | None:
        selected_number = selections.get(approval.candidate_id)
        if selected_number is None:
            return None
        if isinstance(selected_number, bool) or not isinstance(selected_number, int) or selected_number <= 0:
            raise self._failure(
                "external_write_conflict",
                approval,
                "canonical Issue selection must be a positive Issue number",
            )
        selected = [match for match in matches if match.number == selected_number]
        if len(selected) != 1:
            raise self._failure(
                "external_write_conflict",
                approval,
                self._matches_detail(
                    f"canonical Issue selection #{selected_number} was not one of the exact marker matches",
                    matches,
                ),
            )
        return selected[0]

    def _adopt_selected_match(
        self,
        approval: Approval,
        selected: GitHubIssue,
        matches: tuple[GitHubIssue, ...],
        *,
        attempt_id: str,
        write_id: str,
        record_resolution: bool = True,
    ) -> IssueRecord:
        record = self._record_from_remote(approval, selected)
        detail = self._matches_detail(
            f"human selected canonical Issue #{selected.number} without POST",
            matches,
        )
        if record_resolution:
            self.ledger.record_conflict_resolution(
                attempt_id,
                approval,
                write_id=write_id,
                observed_issue_numbers=tuple(match.number for match in matches),
                selected_issue_number=selected.number,
                selected_issue_url=selected.html_url,
                detail=detail,
            )
        self.ledger.record_write_state(
            write_id,
            attempt_id,
            approval,
            "written",
            detail=detail,
            issue_record=record,
        )
        return record

    def _record_from_remote(self, approval: Approval, remote: GitHubIssue) -> IssueRecord:
        marker = marker_for(approval.source_sha256, approval.candidate_id)
        if not isinstance(remote.destination_repository, str) or (
            remote.destination_repository.casefold() != approval.destination_repository.casefold()
        ):
            raise self._failure(
                "external_write_conflict",
                approval,
                "GitHub returned an Issue from a different destination repository",
                stage="verify",
            )
        if not isinstance(remote.number, int) or isinstance(remote.number, bool) or remote.number <= 0:
            raise self._failure(
                "external_write_conflict",
                approval,
                "GitHub returned an invalid Issue number",
                stage="verify",
            )
        if not isinstance(remote.body, str) or not isinstance(remote.html_url, str):
            raise self._failure(
                "external_write_conflict",
                approval,
                "GitHub returned an invalid Issue payload",
                stage="verify",
            )
        if not self._has_single_marker(remote.body, marker):
            raise self._failure(
                "external_write_conflict",
                approval,
                "GitHub returned an Issue without exactly one expected marker",
                stage="verify",
            )
        try:
            return IssueRecord(
                source_sha256=approval.source_sha256,
                candidate_id=approval.candidate_id,
                destination_repository=approval.destination_repository,
                issue_number=remote.number,
                html_url=remote.html_url,
                marker=marker,
                payload_hash=approval.payload_hash,
                recorded_at=datetime.now(UTC).isoformat(),
            )
        except ValidationError as error:
            raise self._failure(
                "external_write_conflict",
                approval,
                "GitHub returned an invalid Issue record",
                stage="verify",
            ) from error

    @staticmethod
    def _has_single_marker(body: str, expected: str) -> bool:
        return body.count(expected) == 1 and body.count("<!-- crework:v1 ") == 1

    @staticmethod
    def _same_issue_record(left: IssueRecord, right: IssueRecord) -> bool:
        return left.model_dump(mode="json", exclude={"recorded_at"}) == right.model_dump(
            mode="json", exclude={"recorded_at"}
        )

    def _approval_for_event(self, event: dict[str, object]) -> Approval | None:
        for approval in self.ledger.approvals:
            if (
                approval.source_sha256 == event.get("source_sha256")
                and approval.candidate_id == event.get("candidate_id")
                and approval.destination_repository == event.get("destination_repository")
                and approval.candidate_snapshot_hash == event.get("candidate_snapshot_hash")
                and approval.payload_hash == event.get("payload_hash")
            ):
                return approval
        return None

    def _latest_for(self, approval: Approval) -> dict[str, object] | None:
        matches = [
            event
            for event in self.ledger.latest_write_attempts()
            if event.get("source_sha256") == approval.source_sha256
            and event.get("candidate_id") == approval.candidate_id
            and event.get("destination_repository") == approval.destination_repository
            and event.get("candidate_snapshot_hash") == approval.candidate_snapshot_hash
            and event.get("payload_hash") == approval.payload_hash
        ]
        return matches[-1] if matches else None

    @staticmethod
    def _failure(
        code: Literal["external_write_failed", "external_write_uncertain", "external_write_conflict"],
        approval: Approval,
        detail: str,
        *,
        stage: str = "reconcile",
        remote_status: int | None = None,
        remote_error: str | None = None,
        next_action: str | None = None,
    ) -> ExternalWriteFailure:
        return ExternalWriteFailure(
            code,
            candidate_id=approval.candidate_id,
            destination_repository=approval.destination_repository,
            candidate_snapshot_hash=approval.candidate_snapshot_hash,
            payload_hash=approval.payload_hash,
            detail=detail,
            stage=stage,
            remote_status=remote_status,
            remote_error=remote_error,
            next_action=next_action,
        )

    @staticmethod
    def _record_failure_from_record(
        record: IssueRecord,
        code: Literal["external_write_failed", "external_write_uncertain", "external_write_conflict"],
        detail: str,
        *,
        stage: str = "reconcile",
        candidate_snapshot_hash: str | None = None,
        remote_status: int | None = None,
        remote_error: str | None = None,
        next_action: str | None = None,
    ) -> ExternalWriteFailure:
        return ExternalWriteFailure(
            code,
            candidate_id=record.candidate_id,
            destination_repository=record.destination_repository,
            candidate_snapshot_hash=candidate_snapshot_hash,
            payload_hash=record.payload_hash,
            detail=detail,
            stage=stage,
            remote_status=remote_status,
            remote_error=remote_error,
            next_action=next_action,
        )
