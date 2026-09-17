import json
import os
from pathlib import Path

import pytest

from feedback_triage.ledger import RunLedger


def test_interaction_id_is_atomically_recorded_before_diagnostics(tmp_path: Path) -> None:
    ledger = RunLedger.create(
        tmp_path, source_sha256="a" * 64, fingerprint="b" * 64, fingerprint_inputs={"source": "a" * 64}
    )
    attempt_id = ledger.start_attempt()

    ledger.record_interaction_created(attempt_id, "interaction-123")
    ledger.record_diagnostic(attempt_id, "step.delta")

    saved = json.loads(ledger.path.read_text())
    attempt = saved["attempts"][0]
    assert attempt["interaction_id"] == "interaction-123"
    assert attempt["diagnostics"] == ["step.delta"]
    assert not list(ledger.path.parent.glob("*.tmp"))


def test_failed_atomic_replace_preserves_previous_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ledger = RunLedger.create(
        tmp_path, source_sha256="a" * 64, fingerprint="b" * 64, fingerprint_inputs={"source": "a" * 64}
    )
    before = ledger.path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))

    with pytest.raises(OSError, match="interrupted"):
        ledger.start_attempt()

    assert ledger.path.read_bytes() == before
    assert not list(ledger.path.parent.glob("*.tmp"))


def test_existing_ledger_rejects_a_different_fingerprint(tmp_path: Path) -> None:
    RunLedger.create(
        tmp_path, source_sha256="a" * 64, fingerprint="b" * 64, fingerprint_inputs={"source": "a" * 64}
    )

    with pytest.raises(ValueError, match="different analysis fingerprint"):
        RunLedger.create(
            tmp_path, source_sha256="a" * 64, fingerprint="c" * 64, fingerprint_inputs={"source": "a" * 64}
        )


def test_fingerprint_inputs_are_saved_with_the_ledger(tmp_path: Path) -> None:
    inputs = {"source": "a" * 64, "model": "model-v1"}

    ledger = RunLedger.create(
        tmp_path,
        source_sha256="a" * 64,
        fingerprint="b" * 64,
        fingerprint_inputs=inputs,
    )

    saved = json.loads(ledger.path.read_text())
    assert saved["fingerprint_inputs"] == inputs


def test_existing_ledger_rejects_corrupt_attempt_shape(tmp_path: Path) -> None:
    source = "a" * 64
    ledger_dir = tmp_path / source
    ledger_dir.mkdir()
    (ledger_dir / "ledger.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source_sha256": source,
                "analysis_fingerprint": "b" * 64,
                "fingerprint_inputs": {"source": source},
                "attempts": [{"status": "verified"}],
            }
        )
    )

    with pytest.raises(ValueError, match="invalid attempt identity"):
        RunLedger.create(
            tmp_path,
            source_sha256=source,
            fingerprint="b" * 64,
            fingerprint_inputs={"source": source},
        )
