from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from feedback_triage.acceptance import (
    ACCEPTANCE_SEQUENCE,
    AcceptanceError,
    AcceptanceStore,
    ManualBaseline,
)


SOURCE_SHA = "a" * 64
FINGERPRINT = "b" * 64


def test_acceptance_requires_the_exact_three_fresh_run_sequence(tmp_path: Path) -> None:
    store = AcceptanceStore(tmp_path / "acceptance.json", source_sha256=SOURCE_SHA, fingerprint=FINGERPRINT)

    assert store.begin("analyze", reanalyze=False) == ACCEPTANCE_SEQUENCE[0]
    store.finish("attempt-1", qualified=True)
    assert store.begin("analyze", reanalyze=True) == ACCEPTANCE_SEQUENCE[1]
    store.finish("attempt-2", qualified=True)
    assert store.begin("run", reanalyze=True) == ACCEPTANCE_SEQUENCE[2]
    store.finish("attempt-3", qualified=True)

    assert store.progress.passed is True
    assert store.progress.next_step is None
    assert store.record.qualified_attempt_ids == ["attempt-1", "attempt-2", "attempt-3"]


def test_nonqualifying_attempt_resets_the_consecutive_requirement(tmp_path: Path) -> None:
    store = AcceptanceStore(tmp_path / "acceptance.json", source_sha256=SOURCE_SHA, fingerprint=FINGERPRINT)
    store.begin("analyze", reanalyze=False)
    store.finish("attempt-1", qualified=True)

    with pytest.raises(AcceptanceError, match="acceptance sequence was reset"):
        store.begin("run", reanalyze=True)
    assert store.progress.qualified_attempt_ids == ()
    reloaded = AcceptanceStore(store.path, source_sha256=SOURCE_SHA, fingerprint=FINGERPRINT)
    assert reloaded.progress.qualified_attempt_ids == ()
    assert reloaded.record.reset_events[-1].reason.startswith("command sequence changed")

    store.begin("analyze", reanalyze=False)
    store.finish("attempt-4", qualified=False, reason="semantic_score_failed")
    assert store.progress.next_step == "analyze"
    assert store.record.reset_events[-1].reason == "semantic_score_failed"


def test_behavior_fingerprint_change_starts_a_new_cycle(tmp_path: Path) -> None:
    path = tmp_path / "acceptance.json"
    first = AcceptanceStore(path, source_sha256=SOURCE_SHA, fingerprint=FINGERPRINT)
    first.begin("analyze", reanalyze=False)
    first.finish("attempt-1", qualified=True)

    second = AcceptanceStore(path, source_sha256=SOURCE_SHA, fingerprint="c" * 64)

    assert second.progress.qualified_attempt_ids == ()
    assert second.record.reset_events[0].reason.startswith("behavior fingerprint changed")


def test_manual_baseline_is_scoped_to_source_and_issue_count(tmp_path: Path) -> None:
    store = AcceptanceStore(tmp_path / "acceptance.json", source_sha256=SOURCE_SHA, fingerprint=FINGERPRINT)
    baseline = ManualBaseline(
        source_sha256=SOURCE_SHA,
        recording_label="previously-unseen-canonical-recording",
        equivalent_issue_count=3,
        equivalent_issue_numbers=(101, 102, 103),
        watch_seconds=360.0,
        issue_writing_seconds=120.0,
        active_human_seconds=480.0,
        measured_at=datetime.now(UTC).isoformat(),
    )

    store.set_manual_baseline(baseline)
    assert store.require_manual_baseline(expected_issue_count=3) == baseline
    with pytest.raises(AcceptanceError, match="only one equivalent"):
        store.set_manual_baseline(baseline.model_copy(update={"recording_label": "another-recording"}))
    with pytest.raises(AcceptanceError, match="expected 2"):
        store.require_manual_baseline(expected_issue_count=2)
