import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, cast
from uuid import uuid4

import fcntl

from feedback_triage.approval import marker_for
from pydantic import ValidationError

from feedback_triage.models import Approval, EvidenceFrameRecord, IssueRecord, RoutedResult, routed_result_hash


class LedgerFingerprintMismatch(ValueError):
    """The persisted ledger does not belong to the current behavior inputs."""


class LedgerInvalid(ValueError):
    """The persisted Run Ledger is corrupt or structurally invalid."""


class LedgerLocked(RuntimeError):
    """Another writer currently owns the source-scoped Run Ledger lock."""


WRITE_STATES = frozenset({"write_pending", "written", "write_failed", "write_uncertain", "external_write_conflict"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LEGACY_SNAPSHOT_HASH = "0" * 64


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _require_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise LedgerInvalid(f"{label} must be a lowercase SHA-256 hash")
    return value


class RunLedger:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self._data = data

    @classmethod
    def create(
        cls,
        output: Path,
        *,
        source_sha256: str,
        fingerprint: str,
        fingerprint_inputs: dict[str, str],
    ) -> "RunLedger":
        path = output / source_sha256 / "ledger.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise LedgerInvalid("existing Run Ledger is not valid JSON") from error
            if not isinstance(data, dict):
                raise LedgerInvalid("existing Run Ledger must be a JSON object")
            cls._upgrade_legacy_snapshot_fields(data)
            cls._validate_existing(data, source_sha256)
            if data["analysis_fingerprint"] != fingerprint:
                raise LedgerFingerprintMismatch("existing Run Ledger has a different analysis fingerprint")
            if data["fingerprint_inputs"] != fingerprint_inputs:
                raise LedgerFingerprintMismatch("existing Run Ledger has different analysis fingerprint inputs")
            return cls(path, data)
        ledger = cls(
            path,
            {
                "version": 1,
                "source_sha256": source_sha256,
                "analysis_fingerprint": fingerprint,
                "fingerprint_inputs": fingerprint_inputs,
                "attempts": [],
                "approvals": [],
                "declines": [],
                "write_attempts": [],
                "issue_records": [],
                "conflict_resolutions": [],
            },
        )
        ledger._write()
        return ledger

    @classmethod
    def load(cls, path: Path) -> "RunLedger":
        """Load a ledger without recomputing the analysis fingerprint."""

        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise LedgerInvalid("Run Ledger is not valid JSON") from error
        if not isinstance(data, dict):
            raise LedgerInvalid("Run Ledger must be a JSON object")
        cls._upgrade_legacy_snapshot_fields(data)
        source_sha256 = data.get("source_sha256")
        if not isinstance(source_sha256, str):
            raise LedgerInvalid("Run Ledger has an invalid source identity")
        cls._validate_existing(data, source_sha256)
        return cls(path, data)

    @staticmethod
    def _upgrade_legacy_snapshot_fields(data: dict[str, Any]) -> None:
        """Fill snapshot hashes introduced after the first write-ledger format.

        Issue #15 persisted the complete reviewed Candidate in each Approval but
        did not persist its hash on approvals, declines, or write events.  Those
        records remain readable so reconciliation can either continue safely or
        fail closed when the old ledger lacks enough evidence to bind a state.
        """

        def identity(value: dict[str, Any]) -> tuple[str, str, str, str] | None:
            source_sha256 = value.get("source_sha256")
            candidate_id = value.get("candidate_id")
            destination_repository = value.get("destination_repository")
            payload_hash = value.get("payload_hash")
            if not isinstance(source_sha256, str):
                return None
            if not isinstance(candidate_id, str):
                return None
            if not isinstance(destination_repository, str):
                return None
            if not isinstance(payload_hash, str):
                return None
            return source_sha256, candidate_id, destination_repository, payload_hash

        approvals = data.get("approvals")
        snapshot_hashes: dict[tuple[str, str, str, str], set[str]] = {}

        def snapshot_for(value: dict[str, Any]) -> str:
            value_identity = identity(value)
            if value_identity is None:
                return LEGACY_SNAPSHOT_HASH
            hashes = snapshot_hashes.get(value_identity, set())
            return next(iter(hashes)) if len(hashes) == 1 else LEGACY_SNAPSHOT_HASH

        if isinstance(approvals, list):
            for value in approvals:
                if not isinstance(value, dict):
                    continue
                snapshot_hash = value.get("candidate_snapshot_hash")
                if snapshot_hash is None:
                    try:
                        snapshot = RoutedResult.model_validate(value.get("candidate_snapshot"))
                    except (TypeError, ValidationError):
                        continue
                    snapshot_hash = routed_result_hash(snapshot)
                    value["candidate_snapshot_hash"] = snapshot_hash
                value_identity = identity(value)
                if value_identity is not None and isinstance(snapshot_hash, str):
                    snapshot_hashes.setdefault(value_identity, set()).add(snapshot_hash)

        for key in ("declines", "reconsiderations", "overrides"):
            values = data.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict) or "candidate_snapshot_hash" in value:
                    continue
                value["candidate_snapshot_hash"] = snapshot_for(value)

        write_attempts = data.get("write_attempts")
        if isinstance(write_attempts, list):
            for value in write_attempts:
                if not isinstance(value, dict) or "candidate_snapshot_hash" in value:
                    continue
                value["candidate_snapshot_hash"] = snapshot_for(value)

    @staticmethod
    def _validate_existing(data: dict[str, Any], source_sha256: str) -> None:
        if data.get("version") != 1:
            raise LedgerInvalid("existing Run Ledger has an unsupported version")
        if data.get("source_sha256") != source_sha256:
            raise LedgerInvalid("existing Run Ledger has a mismatched source identity")
        if not isinstance(data.get("analysis_fingerprint"), str):
            raise LedgerInvalid("existing Run Ledger has an invalid analysis fingerprint")
        fingerprint_inputs = data.get("fingerprint_inputs")
        if not isinstance(fingerprint_inputs, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in fingerprint_inputs.items()
        ):
            raise LedgerInvalid("existing Run Ledger has invalid analysis fingerprint inputs")
        attempts = data.get("attempts")
        if not isinstance(attempts, list):
            raise LedgerInvalid("existing Run Ledger attempts must be a list")
        for attempt in attempts:
            if not isinstance(attempt, dict):
                raise LedgerInvalid("existing Run Ledger contains an invalid attempt")
            if not isinstance(attempt.get("attempt_id"), str) or not isinstance(attempt.get("status"), str):
                raise LedgerInvalid("existing Run Ledger contains an invalid attempt identity")
            if attempt["status"] not in {"starting", "streaming", "verified", "failed"}:
                raise LedgerInvalid("existing Run Ledger contains an invalid attempt status")
            if attempt.get("interaction_id") is not None and not isinstance(attempt["interaction_id"], str):
                raise LedgerInvalid("existing Run Ledger contains an invalid interaction ID")
            if not isinstance(attempt.get("diagnostics"), list) or not all(
                isinstance(item, str) for item in attempt["diagnostics"]
            ):
                raise LedgerInvalid("existing Run Ledger contains invalid diagnostics")
            if attempt["status"] == "verified":
                if not isinstance(attempt.get("analysis"), dict) or not isinstance(
                    attempt.get("processing_pair_count"), int
                ):
                    raise LedgerInvalid("existing verified attempt is incomplete")
            if attempt["status"] == "failed":
                failure = attempt.get("failure")
                if not isinstance(failure, dict) or not isinstance(failure.get("code"), str) or not isinstance(
                    failure.get("detail"), str
                ):
                    raise LedgerInvalid("existing failed attempt is incomplete")
            if "evidence_frames" in attempt:
                frames = attempt["evidence_frames"]
                if not isinstance(frames, list):
                    raise LedgerInvalid("existing attempt has invalid Evidence Frames")
                try:
                    for frame in frames:
                        EvidenceFrameRecord.model_validate(frame)
                except (TypeError, ValidationError) as error:
                    raise LedgerInvalid("existing attempt has invalid Evidence Frames") from error
        for key in (
            "approvals",
            "declines",
            "reconsiderations",
            "write_attempts",
            "issue_records",
            "overrides",
            "reconciliation_errors",
            "conflict_resolutions",
        ):
            if key in data and not isinstance(data[key], list):
                raise LedgerInvalid(f"existing Run Ledger {key} must be a list")
        for approval in data.get("approvals", []):
            if (
                not isinstance(approval, dict)
                or approval.get("state") != "approved"
                or not isinstance(approval.get("attempt_id"), str)
            ):
                raise LedgerInvalid("existing Run Ledger contains an invalid Approval record")
            try:
                Approval.model_validate(_approval_data(approval))
            except (TypeError, ValidationError) as error:
                raise LedgerInvalid("existing Run Ledger contains an invalid Approval") from error
        for key in ("declines", "reconsiderations"):
            for decision in data.get(key, []):
                if not isinstance(decision, dict):
                    raise LedgerInvalid(f"existing Run Ledger contains an invalid {key[:-1]}")
                decision_required = (
                    "attempt_id",
                    "source_sha256",
                    "candidate_id",
                    "destination_repository",
                    "candidate_snapshot_hash",
                    "payload_hash",
                    "recorded_at",
                )
                if not all(isinstance(decision.get(field), str) for field in decision_required):
                    raise LedgerInvalid(f"existing Run Ledger contains an invalid {key[:-1]}")
                if decision["source_sha256"] != source_sha256:
                    raise LedgerInvalid(f"existing Run Ledger contains a mismatched {key[:-1]} source")
                try:
                    _require_hash(decision["candidate_snapshot_hash"], "Candidate snapshot hash")
                    _require_hash(decision["payload_hash"], "payload hash")
                    marker_for(decision["source_sha256"], decision["candidate_id"])
                except (TypeError, ValueError, LedgerInvalid) as error:
                    raise LedgerInvalid(f"existing Run Ledger contains an invalid {key[:-1]}") from error
        for override in data.get("overrides", []):
            if not isinstance(override, dict):
                raise LedgerInvalid("existing Run Ledger contains an invalid override")
            override_required = (
                "attempt_id",
                "write_id",
                "candidate_id",
                "destination_repository",
                "candidate_snapshot_hash",
                "payload_hash",
                "detail",
                "recorded_at",
            )
            if not all(isinstance(override.get(field), str) for field in override_required):
                raise LedgerInvalid("existing Run Ledger contains an invalid override")
            try:
                _require_hash(override["candidate_snapshot_hash"], "Candidate snapshot hash")
                _require_hash(override["payload_hash"], "payload hash")
                marker_for(source_sha256, override["candidate_id"])
            except (TypeError, ValueError, LedgerInvalid) as error:
                raise LedgerInvalid("existing Run Ledger contains an invalid override") from error
        for record in data.get("issue_records", []):
            try:
                IssueRecord.model_validate(record)
            except (TypeError, ValidationError) as error:
                raise LedgerInvalid("existing Run Ledger contains an invalid Issue Record") from error
        for event in data.get("write_attempts", []):
            if not isinstance(event, dict):
                raise LedgerInvalid("existing Run Ledger contains an invalid write event")
            required = (
                "write_id",
                "attempt_id",
                "state",
                "candidate_id",
                "source_sha256",
                "destination_repository",
                "candidate_snapshot_hash",
                "payload_hash",
                "marker",
                "recorded_at",
            )
            if not all(isinstance(event.get(key), str) for key in required):
                raise LedgerInvalid("existing Run Ledger contains an invalid write event")
            if not SHA256_PATTERN.fullmatch(event["candidate_snapshot_hash"]):
                raise LedgerInvalid("existing Run Ledger contains an invalid Candidate snapshot hash")
            state = event["state"]
            if state not in WRITE_STATES:
                raise LedgerInvalid("existing Run Ledger contains an invalid write state")
            try:
                expected_marker = marker_for(event["source_sha256"], event["candidate_id"])
            except (TypeError, ValueError) as error:
                raise LedgerInvalid("existing Run Ledger contains an invalid write identity") from error
            if event["marker"] != expected_marker:
                raise LedgerInvalid("existing Run Ledger contains a mismatched write marker")
            if "stage" in event and not isinstance(event["stage"], str):
                raise LedgerInvalid("existing Run Ledger contains an invalid write stage")
            if "remote_status" in event and (
                not isinstance(event["remote_status"], int) or isinstance(event["remote_status"], bool)
            ):
                raise LedgerInvalid("existing Run Ledger contains an invalid remote status")
            for field in ("remote_error", "next_action"):
                if field in event and not isinstance(event[field], str):
                    raise LedgerInvalid(f"existing Run Ledger contains an invalid {field}")
            if "issue_record" in event:
                try:
                    IssueRecord.model_validate(event["issue_record"])
                except (TypeError, ValidationError) as error:
                    raise LedgerInvalid("existing Run Ledger contains an invalid write Issue Record") from error
            if state == "written" and "issue_record" not in event:
                raise LedgerInvalid("existing written event has no Issue Record")
        for resolution in data.get("conflict_resolutions", []):
            if not isinstance(resolution, dict):
                raise LedgerInvalid("existing Run Ledger contains an invalid conflict resolution")
            resolution_required = (
                "write_id",
                "attempt_id",
                "source_sha256",
                "candidate_id",
                "destination_repository",
                "candidate_snapshot_hash",
                "payload_hash",
                "observed_issue_numbers",
                "selected_issue_number",
                "selected_issue_url",
                "detail",
                "recorded_at",
            )
            if not all(key in resolution for key in resolution_required):
                raise LedgerInvalid("existing Run Ledger contains an incomplete conflict resolution")
            if not all(
                isinstance(resolution.get(key), str)
                for key in resolution_required
                if key not in {"observed_issue_numbers", "selected_issue_number"}
            ):
                raise LedgerInvalid("existing Run Ledger contains an invalid conflict resolution identity")
            if not SHA256_PATTERN.fullmatch(resolution["candidate_snapshot_hash"]):
                raise LedgerInvalid("existing Run Ledger contains an invalid conflict resolution hash")
            numbers = resolution["observed_issue_numbers"]
            if not isinstance(numbers, list) or not all(
                isinstance(number, int) and not isinstance(number, bool) and number > 0 for number in numbers
            ):
                raise LedgerInvalid("existing Run Ledger contains invalid conflict match numbers")
            selected = resolution["selected_issue_number"]
            if not isinstance(selected, int) or isinstance(selected, bool) or selected <= 0:
                raise LedgerInvalid("existing Run Ledger contains an invalid selected Issue number")
            if selected not in numbers:
                raise LedgerInvalid("existing Run Ledger selected Issue is not an observed match")
            if not isinstance(resolution["selected_issue_url"], str) or not resolution["selected_issue_url"].strip():
                raise LedgerInvalid("existing Run Ledger contains an invalid selected Issue URL")
            if not isinstance(resolution["detail"], str) or not resolution["detail"].strip():
                raise LedgerInvalid("existing Run Ledger contains invalid conflict resolution detail")

    def start_attempt(self) -> str:
        attempt_id = str(uuid4())
        self._data["attempts"].append(
            {
                "attempt_id": attempt_id,
                "started_at": _now(),
                "status": "starting",
                "interaction_id": None,
                "diagnostics": [],
                "evidence_frames": [],
            }
        )
        self._write()
        return attempt_id

    def record_interaction_created(self, attempt_id: str, interaction_id: str) -> None:
        attempt = self._attempt(attempt_id)
        attempt["interaction_id"] = interaction_id
        attempt["status"] = "streaming"
        self._write()

    def record_diagnostic(self, attempt_id: str, event_type: str) -> None:
        self._attempt(attempt_id)["diagnostics"].append(event_type)
        self._write()

    @property
    def attempts(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(cast(list[dict[str, Any]], self._data["attempts"])))

    @property
    def source_sha256(self) -> str:
        return cast(str, self._data["source_sha256"])

    def complete(self, attempt_id: str, analysis: dict[str, Any], *, processing_pair_count: int) -> None:
        attempt = self._attempt(attempt_id)
        attempt.update(
            status="verified",
            completed_at=_now(),
            analysis=analysis,
            processing_pair_count=processing_pair_count,
        )
        self._write()

    def record_policy_result(self, attempt_id: str, policy_result: dict[str, Any]) -> None:
        attempt = self._attempt(attempt_id)
        if attempt["status"] != "verified":
            raise ValueError("policy results require a verified analysis")
        attempt["policy_result"] = policy_result
        self._write()

    def record_evidence_frame(self, attempt_id: str, record: EvidenceFrameRecord) -> None:
        attempt = self._attempt(attempt_id)
        if attempt["status"] != "verified":
            raise ValueError("Evidence Frames require a verified analysis")
        frames = attempt.setdefault("evidence_frames", [])
        if not isinstance(frames, list):
            raise LedgerInvalid("attempt has invalid Evidence Frames")
        serialized = record.model_dump(mode="json")
        for index, existing in enumerate(frames):
            if isinstance(existing, dict) and existing.get("candidate_id") == record.candidate_id:
                frames[index] = serialized
                break
        else:
            frames.append(serialized)
        self._write()

    def evidence_frames_for_attempt(self, attempt_id: str) -> tuple[EvidenceFrameRecord, ...]:
        frames = self._attempt(attempt_id).get("evidence_frames", [])
        if not isinstance(frames, list):
            raise LedgerInvalid("attempt has invalid Evidence Frames")
        try:
            return tuple(EvidenceFrameRecord.model_validate(frame) for frame in frames)
        except (TypeError, ValidationError) as error:
            raise LedgerInvalid("attempt has invalid Evidence Frames") from error

    @contextmanager
    def exclusive_lock(self) -> Iterator[None]:
        """Hold the one-workspace, source-scoped lock across review and writes."""

        lock_path = self.path.with_name("ledger.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LedgerLocked(f"Run Ledger is already locked: {self.path}") from error
            try:
                if self.path.exists():
                    self._data = RunLedger.load(self.path)._data
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @property
    def approvals(self) -> tuple[Approval, ...]:
        values = self._data.get("approvals", [])
        if not isinstance(values, list):
            raise LedgerInvalid("Run Ledger approvals must be a list")
        try:
            return tuple(Approval.model_validate(_approval_data(value)) for value in values)
        except (TypeError, ValidationError) as error:
            raise LedgerInvalid("Run Ledger contains an invalid Approval") from error

    @property
    def issue_records(self) -> tuple[IssueRecord, ...]:
        values = self._data.get("issue_records", [])
        if not isinstance(values, list):
            raise LedgerInvalid("Run Ledger issue records must be a list")
        try:
            return tuple(IssueRecord.model_validate(value) for value in values)
        except (TypeError, ValidationError) as error:
            raise LedgerInvalid("Run Ledger contains an invalid Issue Record") from error

    @property
    def write_attempts(self) -> tuple[dict[str, Any], ...]:
        values = self._data.get("write_attempts", [])
        if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
            raise LedgerInvalid("Run Ledger write attempts must be a list of objects")
        return tuple(deepcopy(cast(list[dict[str, Any]], values)))

    @property
    def declines(self) -> tuple[dict[str, Any], ...]:
        values = self._data.get("declines", [])
        if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
            raise LedgerInvalid("Run Ledger declines must be a list of objects")
        return tuple(deepcopy(cast(list[dict[str, Any]], values)))

    def record_approval(self, attempt_id: str, approval: Approval) -> None:
        self._attempt(attempt_id)
        if approval.source_sha256 != self.source_sha256:
            raise LedgerInvalid("Approval source identity does not match the Run Ledger")
        try:
            approval = Approval.model_validate(approval.model_dump(mode="json"))
        except ValidationError as error:
            raise LedgerInvalid("Approval snapshot is invalid") from error
        approvals = self._data.setdefault("approvals", [])
        if not isinstance(approvals, list):
            raise LedgerInvalid("Run Ledger approvals must be a list")
        serialized = {"attempt_id": attempt_id, "state": "approved", **approval.model_dump(mode="json")}
        for existing in approvals:
            if isinstance(existing, dict) and all(existing.get(key) == value for key, value in serialized.items()):
                return
        approvals.append(serialized)
        self._write()

    def latest_approval_for(
        self,
        *,
        candidate_id: str,
        destination_repository: str,
    ) -> Approval | None:
        values = self._data.get("approvals", [])
        if not isinstance(values, list):
            raise LedgerInvalid("Run Ledger approvals must be a list")
        matches: list[Approval] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            if (
                value.get("candidate_id") != candidate_id
                or value.get("destination_repository") != destination_repository
            ):
                continue
            try:
                matches.append(Approval.model_validate(_approval_data(value)))
            except (TypeError, ValidationError) as error:
                raise LedgerInvalid("Run Ledger contains an invalid Approval") from error
        return matches[-1] if matches else None

    def record_decline(
        self,
        attempt_id: str,
        *,
        source_sha256: str,
        candidate_id: str,
        destination_repository: str,
        candidate_snapshot_hash: str,
        payload_hash: str,
        recorded_at: str | None = None,
        reason: str = "declined",
    ) -> None:
        self._attempt(attempt_id)
        if source_sha256 != self.source_sha256:
            raise LedgerInvalid("decline source identity does not match the Run Ledger")
        _require_hash(candidate_snapshot_hash, "Candidate snapshot hash")
        _require_hash(payload_hash, "payload hash")
        declines = self._data.setdefault("declines", [])
        if not isinstance(declines, list):
            raise LedgerInvalid("Run Ledger declines must be a list")
        declines.append(
            {
                "attempt_id": attempt_id,
                "source_sha256": source_sha256,
                "candidate_id": candidate_id,
                "destination_repository": destination_repository,
                "candidate_snapshot_hash": candidate_snapshot_hash,
                "payload_hash": payload_hash,
                "reason": reason,
                "recorded_at": recorded_at or _now(),
            }
        )
        self._write()

    def has_decline(
        self,
        *,
        source_sha256: str,
        candidate_id: str,
        destination_repository: str,
        candidate_snapshot_hash: str,
        payload_hash: str,
    ) -> bool:
        return any(
            value.get("source_sha256") == source_sha256
            and value.get("candidate_id") == candidate_id
            and value.get("destination_repository") == destination_repository
            and value.get("payload_hash") == payload_hash
            and (
                value.get("candidate_snapshot_hash") == candidate_snapshot_hash
                or value.get("candidate_snapshot_hash") == LEGACY_SNAPSHOT_HASH
            )
            for value in self.declines
        )

    def record_reconsideration(
        self,
        attempt_id: str,
        *,
        source_sha256: str,
        candidate_id: str,
        destination_repository: str,
        candidate_snapshot_hash: str,
        payload_hash: str,
        recorded_at: str | None = None,
    ) -> None:
        self._attempt(attempt_id)
        if source_sha256 != self.source_sha256:
            raise LedgerInvalid("reconsideration source identity does not match the Run Ledger")
        _require_hash(candidate_snapshot_hash, "Candidate snapshot hash")
        _require_hash(payload_hash, "payload hash")
        reconsiderations = self._data.setdefault("reconsiderations", [])
        if not isinstance(reconsiderations, list):
            raise LedgerInvalid("Run Ledger reconsiderations must be a list")
        reconsiderations.append(
            {
                "attempt_id": attempt_id,
                "source_sha256": source_sha256,
                "candidate_id": candidate_id,
                "destination_repository": destination_repository,
                "candidate_snapshot_hash": candidate_snapshot_hash,
                "payload_hash": payload_hash,
                "recorded_at": recorded_at or _now(),
            }
        )
        self._write()

    def record_write_pending(self, attempt_id: str, approval: Approval) -> str:
        self._attempt(attempt_id)
        write_id = str(uuid4())
        self._append_write_state(
            write_id,
            attempt_id,
            approval,
            "write_pending",
        )
        return write_id

    def record_write_state(
        self,
        write_id: str,
        attempt_id: str,
        approval: Approval,
        state: str,
        *,
        detail: str | None = None,
        issue_record: IssueRecord | None = None,
        stage: str | None = None,
        remote_status: int | None = None,
        remote_error: str | None = None,
        next_action: str | None = None,
    ) -> None:
        if state not in {"written", "write_failed", "write_uncertain", "external_write_conflict"}:
            raise ValueError(f"invalid terminal write state: {state}")
        self._attempt(attempt_id)
        if state == "written" and issue_record is None:
            raise ValueError("written state requires an Issue Record")
        if issue_record is not None:
            self._record_issue_record(issue_record)
        self._append_write_state(
            write_id,
            attempt_id,
            approval,
            state,
            detail=detail,
            issue_record=issue_record,
            stage=stage,
            remote_status=remote_status,
            remote_error=remote_error,
            next_action=next_action,
        )

    def record_override(
        self,
        attempt_id: str,
        approval: Approval,
        *,
        write_id: str,
        detail: str,
    ) -> None:
        self._attempt(attempt_id)
        if approval.source_sha256 != self.source_sha256:
            raise LedgerInvalid("override source identity does not match the Run Ledger")
        overrides = self._data.setdefault("overrides", [])
        if not isinstance(overrides, list):
            raise LedgerInvalid("Run Ledger overrides must be a list")
        overrides.append(
            {
                "attempt_id": attempt_id,
                "write_id": write_id,
                "candidate_id": approval.candidate_id,
                "destination_repository": approval.destination_repository,
                "candidate_snapshot_hash": approval.candidate_snapshot_hash,
                "payload_hash": approval.payload_hash,
                "detail": detail,
                "recorded_at": _now(),
            }
        )
        self._write()

    def record_conflict_resolution(
        self,
        attempt_id: str,
        approval: Approval,
        *,
        write_id: str,
        observed_issue_numbers: tuple[int, ...],
        selected_issue_number: int,
        selected_issue_url: str,
        detail: str,
    ) -> None:
        self._attempt(attempt_id)
        if approval.source_sha256 != self.source_sha256:
            raise LedgerInvalid("conflict resolution source identity does not match the Run Ledger")
        if not observed_issue_numbers or any(
            isinstance(number, bool) or not isinstance(number, int) or number <= 0
            for number in observed_issue_numbers
        ):
            raise LedgerInvalid("conflict resolution requires positive observed Issue numbers")
        if selected_issue_number not in observed_issue_numbers:
            raise LedgerInvalid("selected Issue must be one of the observed marker matches")
        if not selected_issue_url.strip() or not detail.strip():
            raise LedgerInvalid("conflict resolution URL and detail must not be blank")
        resolutions = self._data.setdefault("conflict_resolutions", [])
        if not isinstance(resolutions, list):
            raise LedgerInvalid("Run Ledger conflict resolutions must be a list")
        resolutions.append(
            {
                "write_id": write_id,
                "attempt_id": attempt_id,
                "source_sha256": approval.source_sha256,
                "candidate_id": approval.candidate_id,
                "destination_repository": approval.destination_repository,
                "candidate_snapshot_hash": approval.candidate_snapshot_hash,
                "payload_hash": approval.payload_hash,
                "observed_issue_numbers": list(observed_issue_numbers),
                "selected_issue_number": selected_issue_number,
                "selected_issue_url": selected_issue_url,
                "detail": detail,
                "recorded_at": _now(),
            }
        )
        self._write()

    def conflict_resolution_for(self, write_id: str) -> dict[str, Any] | None:
        values = self._data.get("conflict_resolutions", [])
        if not isinstance(values, list):
            raise LedgerInvalid("Run Ledger conflict resolutions must be a list")
        matches = [value for value in values if isinstance(value, dict) and value.get("write_id") == write_id]
        return deepcopy(matches[-1]) if matches else None

    def record_reconciliation_error(
        self,
        *,
        source_sha256: str,
        candidate_id: str,
        destination_repository: str,
        code: str,
        detail: str,
        candidate_snapshot_hash: str | None = None,
        stage: str = "reconcile",
        remote_status: int | None = None,
        remote_error: str | None = None,
        next_action: str | None = None,
    ) -> None:
        errors = self._data.setdefault("reconciliation_errors", [])
        if not isinstance(errors, list):
            raise LedgerInvalid("Run Ledger reconciliation errors must be a list")
        errors.append(
            {
                "source_sha256": source_sha256,
                "candidate_id": candidate_id,
                "destination_repository": destination_repository,
                "code": code,
                "detail": detail,
                "candidate_snapshot_hash": candidate_snapshot_hash,
                "stage": stage,
                "remote_status": remote_status,
                "remote_error": remote_error,
                "next_action": next_action,
                "recorded_at": _now(),
            }
        )
        self._write()

    def latest_write_attempts(self) -> tuple[dict[str, Any], ...]:
        latest: dict[str, dict[str, Any]] = {}
        for attempt in self.write_attempts:
            write_id = attempt.get("write_id")
            if isinstance(write_id, str):
                latest[write_id] = attempt
        return tuple(deepcopy(tuple(latest.values())))

    def issue_record_for(self, approval: Approval) -> IssueRecord | None:
        matches = [
            record
            for record in self.issue_records
            if record.source_sha256 == approval.source_sha256
            and record.candidate_id == approval.candidate_id
            and record.destination_repository == approval.destination_repository
        ]
        if len(matches) > 1 and any(record != matches[0] for record in matches[1:]):
            raise LedgerInvalid("Run Ledger contains conflicting Issue Records")
        return matches[0] if matches else None

    def snapshot_hash_for_record(self, record: IssueRecord) -> str | None:
        for event in reversed(self.write_attempts):
            if (
                event.get("source_sha256") == record.source_sha256
                and event.get("candidate_id") == record.candidate_id
                and event.get("destination_repository") == record.destination_repository
                and event.get("payload_hash") == record.payload_hash
            ):
                value = event.get("candidate_snapshot_hash")
                if isinstance(value, str):
                    return value
        return None

    def _append_write_state(
        self,
        write_id: str,
        attempt_id: str,
        approval: Approval,
        state: str,
        *,
        detail: str | None = None,
        issue_record: IssueRecord | None = None,
        stage: str | None = None,
        remote_status: int | None = None,
        remote_error: str | None = None,
        next_action: str | None = None,
    ) -> None:
        attempts = self._data.setdefault("write_attempts", [])
        if not isinstance(attempts, list):
            raise LedgerInvalid("Run Ledger write attempts must be a list")
        value: dict[str, Any] = {
            "write_id": write_id,
            "attempt_id": attempt_id,
            "state": state,
            "candidate_id": approval.candidate_id,
            "source_sha256": approval.source_sha256,
            "destination_repository": approval.destination_repository,
            "candidate_snapshot_hash": approval.candidate_snapshot_hash,
            "payload_hash": approval.payload_hash,
            "marker": _marker_from_approval(approval),
            "recorded_at": _now(),
        }
        if detail is not None:
            value["detail"] = detail
        if issue_record is not None:
            value["issue_record"] = issue_record.model_dump(mode="json")
        if stage is not None:
            value["stage"] = stage
        if remote_status is not None:
            value["remote_status"] = remote_status
        if remote_error is not None:
            value["remote_error"] = remote_error
        if next_action is not None:
            value["next_action"] = next_action
        attempts.append(value)
        self._write()

    def _record_issue_record(self, record: IssueRecord) -> None:
        records = self._data.setdefault("issue_records", [])
        if not isinstance(records, list):
            raise LedgerInvalid("Run Ledger issue records must be a list")
        for existing in records:
            if not isinstance(existing, dict):
                raise LedgerInvalid("Run Ledger contains an invalid Issue Record")
            same_identity = all(
                existing.get(key) == getattr(record, key)
                for key in ("source_sha256", "candidate_id", "destination_repository")
            )
            if same_identity:
                if not _same_issue_record(existing, record):
                    raise LedgerInvalid("Run Ledger contains conflicting Issue Records")
                return
        records.append(record.model_dump(mode="json"))

    def fail(self, attempt_id: str, *, code: str, detail: str) -> None:
        attempt = self._attempt(attempt_id)
        attempt.pop("policy_result", None)
        attempt.update(status="failed", completed_at=_now(), failure={"code": code, "detail": detail})
        self._write()

    def _attempt(self, attempt_id: str) -> dict[str, Any]:
        for attempt in self._data["attempts"]:
            if attempt["attempt_id"] == attempt_id:
                return cast(dict[str, Any], attempt)
        raise KeyError(f"unknown attempt: {attempt_id}")

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="ledger-", suffix=".tmp", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(self._data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary.unlink(missing_ok=True)


def _approval_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Approval record must be an object")
    return {key: item for key, item in value.items() if key not in {"attempt_id", "state"}}


def _marker_from_approval(approval: Approval) -> str:
    return marker_for(approval.source_sha256, approval.candidate_id)


def _same_issue_record(existing: dict[str, Any], record: IssueRecord) -> bool:
    expected = record.model_dump(mode="json")
    return all(
        existing.get(key) == value
        for key, value in expected.items()
        if key != "recorded_at"
    )
