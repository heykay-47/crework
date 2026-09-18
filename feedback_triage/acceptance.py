"""Durable proof state for the frozen three-analysis acceptance cycle."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from .models import StrictModel
from .persistence import atomic_write_json


AcceptanceStep = Literal["analyze", "analyze --reanalyze", "run --reanalyze"]
ACCEPTANCE_SEQUENCE: tuple[AcceptanceStep, ...] = (
    "analyze",
    "analyze --reanalyze",
    "run --reanalyze",
)


class AcceptanceError(ValueError):
    """The requested command cannot advance the frozen acceptance cycle."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class ManualBaseline(StrictModel):
    """Measured manual work for the same recording and equivalent outputs."""

    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recording_label: str = Field(min_length=1)
    equivalent_issue_count: int = Field(gt=0)
    equivalent_issue_numbers: tuple[int, ...] = Field(min_length=1)
    watch_seconds: float = Field(gt=0)
    issue_writing_seconds: float = Field(gt=0)
    active_human_seconds: float = Field(gt=0)
    measured_at: str = Field(min_length=1)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_baseline(self) -> "ManualBaseline":
        try:
            timestamp = datetime.fromisoformat(self.measured_at)
        except ValueError as error:
            raise ValueError("manual baseline timestamp must be ISO-8601") from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("manual baseline timestamp must include a timezone")
        if any(
            not math.isfinite(value)
            for value in (self.watch_seconds, self.issue_writing_seconds, self.active_human_seconds)
        ):
            raise ValueError("manual baseline durations must be finite")
        if self.notes is not None and not self.notes.strip():
            raise ValueError("manual baseline notes must not be blank")
        if len(self.equivalent_issue_numbers) != self.equivalent_issue_count:
            raise ValueError("manual baseline Issue numbers must match the equivalent Issue count")
        if len(set(self.equivalent_issue_numbers)) != len(self.equivalent_issue_numbers):
            raise ValueError("manual baseline Issue numbers must be unique")
        if any(number <= 0 for number in self.equivalent_issue_numbers):
            raise ValueError("manual baseline Issue numbers must be positive")
        return self


class AcceptanceReset(StrictModel):
    reason: str = Field(min_length=1)
    recorded_at: str = Field(min_length=1)
    attempt_id: str | None = None


class AcceptanceMeasurement(StrictModel):
    attempt_id: str = Field(min_length=1)
    qualified: bool
    reason: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metrics(self) -> "AcceptanceMeasurement":
        for name, value in self.metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"acceptance measurement {name!r} must be numeric")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"acceptance measurement {name!r} must be finite and non-negative")
        return self


class AcceptanceRecord(StrictModel):
    version: Literal[1] = 1
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualified_attempt_ids: list[str] = Field(default_factory=list)
    reset_events: list[AcceptanceReset] = Field(default_factory=list)
    measurements: list[AcceptanceMeasurement] = Field(default_factory=list)
    manual_baseline: ManualBaseline | None = None
    passed: bool = False


@dataclass(frozen=True, slots=True)
class AcceptanceProgress:
    """Read-only view used by the CLI and deterministic tests."""

    qualified_attempt_ids: tuple[str, ...]
    passed: bool

    @property
    def next_step(self) -> AcceptanceStep | None:
        if self.passed:
            return None
        if len(self.qualified_attempt_ids) >= len(ACCEPTANCE_SEQUENCE):
            return None
        return ACCEPTANCE_SEQUENCE[len(self.qualified_attempt_ids)]


class AcceptanceStore:
    """Atomically persist acceptance progress beside a source Run Ledger."""

    def __init__(self, path: Path, *, source_sha256: str, fingerprint: str) -> None:
        self.path = path
        self.source_sha256 = source_sha256
        self.fingerprint = fingerprint
        self._record = self._load_or_initialize()

    @property
    def record(self) -> AcceptanceRecord:
        return self._record.model_copy(deep=True)

    @property
    def progress(self) -> AcceptanceProgress:
        return AcceptanceProgress(tuple(self._record.qualified_attempt_ids), self._record.passed)

    def begin(self, command: Literal["analyze", "run"], *, reanalyze: bool) -> AcceptanceStep:
        actual = cast(AcceptanceStep, command if not reanalyze else f"{command} --reanalyze")
        expected = self.progress.next_step
        if expected is None:
            raise AcceptanceError("acceptance_complete", "the frozen three-run acceptance cycle already passed")
        if actual != expected:
            self._reset(
                "command sequence changed; restart with analyze, analyze --reanalyze, then run --reanalyze"
            )
            self._write()
            raise AcceptanceError(
                "acceptance_sequence",
                f"expected {expected!r}, received {actual!r}; the acceptance sequence was reset",
            )
        return expected

    def finish(
        self,
        attempt_id: str,
        *,
        qualified: bool,
        reason: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self._record.measurements.append(
            AcceptanceMeasurement(attempt_id=attempt_id or "unknown", qualified=qualified, reason=reason, metrics=metrics or {})
        )
        if qualified:
            if not attempt_id:
                raise AcceptanceError("acceptance_attempt", "a qualifying attempt requires an attempt ID")
            if attempt_id in self._record.qualified_attempt_ids:
                raise AcceptanceError("acceptance_attempt", "an attempt cannot qualify twice")
            self._record.qualified_attempt_ids.append(attempt_id)
            self._record.passed = len(self._record.qualified_attempt_ids) == len(ACCEPTANCE_SEQUENCE)
        else:
            self._reset(reason or "attempt did not satisfy the acceptance contract", attempt_id=attempt_id or None)
        self._write()

    def set_manual_baseline(self, baseline: ManualBaseline) -> None:
        if baseline.source_sha256 != self.source_sha256:
            raise AcceptanceError("manual_baseline", "manual baseline source identity does not match the recording")
        if self._record.manual_baseline is not None and self._record.manual_baseline != baseline:
            raise AcceptanceError("manual_baseline", "only one equivalent manual baseline may be recorded for a source")
        self._record.manual_baseline = baseline
        self._write()

    def require_manual_baseline(self, *, expected_issue_count: int) -> ManualBaseline:
        baseline = self._record.manual_baseline
        if baseline is None:
            raise AcceptanceError("manual_baseline", "the acceptance cycle requires a measured manual baseline")
        if baseline.source_sha256 != self.source_sha256:
            raise AcceptanceError("manual_baseline", "manual baseline source identity does not match the recording")
        if baseline.equivalent_issue_count != expected_issue_count:
            raise AcceptanceError(
                "manual_baseline",
                f"manual baseline covers {baseline.equivalent_issue_count} Issues; expected {expected_issue_count}",
            )
        return baseline

    def _load_or_initialize(self) -> AcceptanceRecord:
        if not self.path.exists():
            return AcceptanceRecord(source_sha256=self.source_sha256, fingerprint=self.fingerprint)
        try:
            record = AcceptanceRecord.model_validate_json(self.path.read_text())
        except (OSError, ValueError) as error:
            raise AcceptanceError("acceptance_invalid", "acceptance proof state is not valid JSON") from error
        if record.source_sha256 != self.source_sha256:
            raise AcceptanceError("acceptance_invalid", "acceptance proof state has a mismatched source identity")
        if record.fingerprint != self.fingerprint:
            old_fingerprint = record.fingerprint
            record = AcceptanceRecord(
                source_sha256=self.source_sha256,
                fingerprint=self.fingerprint,
                reset_events=[
                    AcceptanceReset(
                        reason=f"behavior fingerprint changed from {old_fingerprint} to {self.fingerprint}",
                        recorded_at=datetime.now(UTC).isoformat(),
                    )
                ],
            )
            self._record = record
            self._write()
        return record

    def _reset(self, reason: str, *, attempt_id: str | None = None) -> None:
        self._record.qualified_attempt_ids.clear()
        self._record.passed = False
        self._record.reset_events.append(
            AcceptanceReset(reason=reason, recorded_at=datetime.now(UTC).isoformat(), attempt_id=attempt_id)
        )

    def _write(self) -> None:
        atomic_write_json(self.path, self._record.model_dump(mode="json"), prefix="acceptance-")
