import json
import os
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LedgerFingerprintMismatch(ValueError):
    """The persisted ledger does not belong to the current behavior inputs."""


class LedgerInvalid(ValueError):
    """The persisted Run Ledger is corrupt or structurally invalid."""


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
            },
        )
        ledger._write()
        return ledger

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

    def start_attempt(self) -> str:
        attempt_id = str(uuid4())
        self._data["attempts"].append(
            {
                "attempt_id": attempt_id,
                "started_at": _now(),
                "status": "starting",
                "interaction_id": None,
                "diagnostics": [],
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
