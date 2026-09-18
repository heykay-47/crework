"""Pure approval and Issue-payload construction rules.

This module deliberately has no GitHub or ledger dependency.  The write
coordinator is responsible for persistence and remote side effects; these
functions make the immutable decision boundary explicit and easy to test.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .models import (
    Approval,
    IssuePayload,
    RoutedResult,
    issue_payload_hash,
)

SOURCE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CANDIDATE_ID_PATTERN = re.compile(r"^cand_[0-9a-f]{16}$")
EDITABLE_FIELDS = frozenset(
    {
        "title",
        "summary",
        "requested_outcome",
        "acceptance_criteria",
        "component",
    }
)


class ApprovalError(ValueError):
    """Raised when a review decision violates the approval contract."""


def normalize_destination(destination_repository: str) -> str:
    value = destination_repository.strip()
    parts = value.split("/")
    if len(parts) != 2 or any(not part for part in parts):
        raise ApprovalError("destination must be an owner/repository pair")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ApprovalError("destination contains an invalid GitHub owner or repository")
    return "/".join(part.lower() for part in parts)


def marker_for(source_sha256: str, candidate_id: str) -> str:
    if not SOURCE_SHA256_PATTERN.fullmatch(source_sha256):
        raise ApprovalError("source SHA-256 must be 64 lowercase hexadecimal characters")
    if not CANDIDATE_ID_PATTERN.fullmatch(candidate_id):
        raise ApprovalError("Candidate ID has an invalid format")
    return f"<!-- crework:v1 source_sha256={source_sha256} candidate_id={candidate_id} -->"


def payload_hash(payload: IssuePayload) -> str:
    """Return the canonical hash bound into an Approval snapshot."""

    return issue_payload_hash(payload)


def render_issue_payload(candidate: RoutedResult, source_sha256: str) -> IssuePayload:
    marker = marker_for(source_sha256, candidate.candidate_id)
    evidence_lines: list[str] = []
    for evidence in candidate.evidence:
        quote = evidence.client_quote or "(no client quote)"
        visual = evidence.visual_observation or "(no visual observation)"
        evidence_lines.append(
            "- "
            f"{evidence.start_seconds:.3f}s to {evidence.end_seconds:.3f}s: "
            f"{quote}; visual: {visual}"
        )

    acceptance = "\n".join(f"- {criterion}" for criterion in candidate.acceptance_criteria)
    if not acceptance:
        acceptance = "- No acceptance criterion was provided."
    requested_outcome = candidate.requested_outcome or "No requested outcome was provided."
    body = "\n".join(
        [
            marker,
            "",
            "## Summary",
            candidate.summary,
            "",
            "## Requested outcome",
            requested_outcome,
            "",
            "## Acceptance criteria",
            acceptance,
            "",
            "## Component",
            candidate.component or "Not specified",
            "",
            "## Source evidence",
            f"- Source SHA-256: `{source_sha256}`",
            f"- Candidate ID: `{candidate.candidate_id}`",
            f"- Topic: `{candidate.topic_key}`",
            f"- Evidence Frame: {candidate.evidence_frame_seconds if candidate.evidence_frame_seconds is not None else 'not requested'}",
            f"- Visually inferred: `{candidate.visually_inferred}`",
            *evidence_lines,
            "",
            "## Triage context",
            f"- Type: `{candidate.type}`",
            f"- Intent: `{candidate.intent}`",
            f"- Confidence: `{candidate.confidence}`",
            f"- Rationale: {candidate.rationale}",
        ]
    )
    return IssuePayload(title=candidate.title.strip(), body=body)


def edit_candidate(candidate: RoutedResult, changes: Mapping[str, Any]) -> RoutedResult:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ApprovalError(f"immutable Candidate fields cannot be edited: {names}")
    try:
        return RoutedResult.model_validate({**candidate.model_dump(mode="python"), **dict(changes)})
    except ValueError as error:
        raise ApprovalError(f"Candidate edit is invalid: {error}") from error


def build_approval(
    source_sha256: str,
    candidate: RoutedResult,
    destination_repository: str,
    *,
    changes: Mapping[str, Any] | None = None,
    manual_review_confirmed: bool = False,
    operator_label: str | None = None,
    approved_at: str | None = None,
) -> Approval:
    """Render and validate one explicit, immutable Approval snapshot."""

    destination = normalize_destination(destination_repository)
    reviewed_candidate = candidate if changes is None else edit_candidate(candidate, changes)
    confirmed = manual_review_confirmed or changes is not None
    if reviewed_candidate.route == "manual_review" and not confirmed:
        raise ApprovalError("Manual Review must be confirmed or edited before Approval")
    if reviewed_candidate.route not in {"candidate", "manual_review"}:
        raise ApprovalError("Clarification Requests and Withheld Results cannot be approved")
    if reviewed_candidate.route == "candidate" and not reviewed_candidate.approval_eligible:
        raise ApprovalError("only policy-admitted Candidates may be approved")
    candidate_payload = render_issue_payload(candidate, source_sha256)
    payload = render_issue_payload(reviewed_candidate, source_sha256)
    timestamp = approved_at or datetime.now(UTC).isoformat()
    return Approval(
        source_sha256=source_sha256,
        candidate_id=reviewed_candidate.candidate_id,
        destination_repository=destination,
        candidate_snapshot=reviewed_candidate,
        candidate_payload_hash=payload_hash(candidate_payload),
        payload=payload,
        payload_hash=payload_hash(payload),
        approved_at=timestamp,
        operator_label=operator_label,
        manual_review_confirmed=confirmed,
    )
