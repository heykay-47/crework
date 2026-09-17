import json
import os
from pathlib import Path

import pytest

from feedback_triage.ledger import RunLedger
from feedback_triage.models import EvidenceFrameRecord


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


def verified_ledger(tmp_path: Path) -> tuple[RunLedger, str]:
    ledger = RunLedger.create(
        tmp_path, source_sha256="a" * 64, fingerprint="b" * 64, fingerprint_inputs={"source": "a" * 64}
    )
    attempt_id = ledger.start_attempt()
    ledger.complete(
        attempt_id,
        {"schema_version": "1.0", "video_summary": "summary", "observations": []},
        processing_pair_count=1,
    )
    ledger.record_policy_result(attempt_id, {"schema_version": "1.0", "results": []})
    return ledger, attempt_id


def test_evidence_frame_failure_is_persisted_without_terminalizing_attempt(tmp_path: Path) -> None:
    ledger, attempt_id = verified_ledger(tmp_path)
    record = EvidenceFrameRecord(
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=12.5,
        status="failed",
        error="ffmpeg exited with status 1",
    )

    ledger.record_evidence_frame(attempt_id, record)

    attempt = ledger.attempts[0]
    assert attempt["status"] == "verified"
    assert attempt["policy_result"] == {"schema_version": "1.0", "results": []}
    assert ledger.evidence_frames_for_attempt(attempt_id) == (record,)
    saved = json.loads(ledger.path.read_text())
    assert saved["attempts"][0]["evidence_frames"] == [record.model_dump(mode="json")]


def test_evidence_frames_upsert_by_candidate_and_remain_scoped_to_attempt(tmp_path: Path) -> None:
    ledger, first_attempt = verified_ledger(tmp_path)
    second_attempt = ledger.start_attempt()
    ledger.complete(
        second_attempt,
        {"schema_version": "1.0", "video_summary": "summary", "observations": []},
        processing_pair_count=1,
    )
    ledger.record_policy_result(second_attempt, {"schema_version": "1.0", "results": []})
    first = EvidenceFrameRecord(
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=12.5,
        status="failed",
        error="first attempt failed",
    )
    replacement = EvidenceFrameRecord(
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=13.5,
        status="extracted",
        path="frames/navbar.png",
    )
    second = EvidenceFrameRecord(
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=20.0,
        status="failed",
        error="second attempt failed",
    )

    ledger.record_evidence_frame(first_attempt, first)
    ledger.record_evidence_frame(first_attempt, replacement)
    ledger.record_evidence_frame(second_attempt, second)

    assert ledger.evidence_frames_for_attempt(first_attempt) == (replacement,)
    assert ledger.evidence_frames_for_attempt(second_attempt) == (second,)


def test_legacy_verified_attempt_without_evidence_frames_remains_readable(tmp_path: Path) -> None:
    ledger, attempt_id = verified_ledger(tmp_path)
    saved = json.loads(ledger.path.read_text())
    del saved["attempts"][0]["evidence_frames"]
    ledger.path.write_text(json.dumps(saved))

    reloaded = RunLedger.create(
        tmp_path,
        source_sha256="a" * 64,
        fingerprint="b" * 64,
        fingerprint_inputs={"source": "a" * 64},
    )

    assert reloaded.evidence_frames_for_attempt(attempt_id) == ()
