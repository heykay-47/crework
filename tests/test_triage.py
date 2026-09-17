import json
from pathlib import Path

import pytest

from feedback_triage.analyze import AnalysisFailed, analysis_fingerprint, file_sha256, triage_recording
from feedback_triage.input_video import VideoInfo
from feedback_triage.ledger import RunLedger
from main import main
from tests.test_completion_trust import VALID_OUTPUT


def prepared_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, analysis: dict[str, object]) -> tuple[Path, RunLedger]:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"owned synthetic recording")
    monkeypatch.setattr("feedback_triage.analyze.probe_video", lambda path: VideoInfo(path=path, duration_seconds=10.0))
    source_sha256 = file_sha256(video)
    ledger = RunLedger.create(
        tmp_path / "output",
        source_sha256=source_sha256,
        fingerprint=analysis_fingerprint(source_sha256),
    )
    attempt_id = ledger.start_attempt()
    ledger.complete(attempt_id, analysis, processing_pair_count=1)
    return video, ledger


def test_verified_analysis_is_routed_and_every_result_is_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video, ledger = prepared_ledger(monkeypatch, tmp_path, json.loads(VALID_OUTPUT))

    _, verified, policy, returned_ledger = triage_recording(video, tmp_path / "output")

    assert verified.processing_pair_count == 1
    assert policy.results[0].route == "candidate"
    assert policy.results[0].approval_eligible is True
    assert returned_ledger.path == ledger.path
    saved = json.loads(ledger.path.read_text())
    assert saved["attempts"][0]["policy_result"] == policy.model_dump(mode="json")


def test_policy_failure_is_recorded_without_routes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    invalid = json.loads(VALID_OUTPUT)
    invalid["observations"][0]["topic_key"] = "Not Kebab Case"
    video, ledger = prepared_ledger(monkeypatch, tmp_path, invalid)

    with pytest.raises(AnalysisFailed) as failure:
        triage_recording(video, tmp_path / "output")

    assert failure.value.code == "policy_failed"
    saved_attempt = json.loads(ledger.path.read_text())["attempts"][0]
    assert saved_attempt["status"] == "failed"
    assert saved_attempt["failure"]["code"] == "policy_failed"
    assert "policy_result" not in saved_attempt


def test_terminal_output_shows_every_route_and_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    low_confidence = json.loads(VALID_OUTPUT)
    low_confidence["observations"][0]["confidence"] = "low"
    video, _ = prepared_ledger(monkeypatch, tmp_path, low_confidence)

    exit_code = main(["analyze", str(video), "--output", str(tmp_path / "output")])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready_for_review"
    assert output["policy_result"]["results"][0]["route"] == "withheld_result"
    assert output["policy_result"]["results"][0]["reason_code"] == "low_confidence"
    assert output["policy_result"]["results"][0]["approval_eligible"] is False
