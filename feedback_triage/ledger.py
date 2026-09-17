import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunLedger:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self._data = data

    @classmethod
    def create(cls, output: Path, *, source_sha256: str, fingerprint: str) -> "RunLedger":
        path = output / source_sha256 / "ledger.json"
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("analysis_fingerprint") != fingerprint:
                raise ValueError("existing Run Ledger has a different analysis fingerprint")
            return cls(path, data)
        ledger = cls(
            path,
            {
                "version": 1,
                "source_sha256": source_sha256,
                "analysis_fingerprint": fingerprint,
                "attempts": [],
            },
        )
        ledger._write()
        return ledger

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

    def complete(self, attempt_id: str, analysis: dict[str, Any]) -> None:
        attempt = self._attempt(attempt_id)
        attempt.update(status="verified", completed_at=_now(), analysis=analysis)
        self._write()

    def fail(self, attempt_id: str, *, code: str, detail: str) -> None:
        attempt = self._attempt(attempt_id)
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
