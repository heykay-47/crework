import json
from pathlib import Path

import pytest

from feedback_triage.analyze import (
    AnalysisFailed,
    analysis_fingerprint,
    analysis_fingerprint_inputs,
    file_sha256,
    triage_recording,
)
from feedback_triage.input_video import VideoInfo
from feedback_triage.ledger import RunLedger
from feedback_triage.models import AnalysisResult, VerifiedAnalysis
from feedback_triage.policy import route_analysis
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
        fingerprint_inputs=analysis_fingerprint_inputs(source_sha256),
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


def test_analysis_fingerprint_changes_when_behavior_inputs_change() -> None:
    source_sha256 = "a" * 64
    inputs = analysis_fingerprint_inputs(source_sha256, project_context="context", fixture_version="fixture-v1")

    assert {"source", "model", "prompt", "schema", "context", "policy", "dependency", "implementation"} <= set(inputs)
    assert analysis_fingerprint(source_sha256, project_context="context", fixture_version="fixture-v1") != analysis_fingerprint(
        source_sha256,
        project_context="changed context",
        fixture_version="fixture-v1",
    )


@pytest.mark.parametrize(
    ("expected_route", "expected_status", "expected_exit_code"),
    [("candidate", "ready_for_review", 0), ("manual_review", "semantic_score_failed", 1)],
)
def test_terminal_semantic_score_controls_exit_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    expected_route: str,
    expected_status: str,
    expected_exit_code: int,
) -> None:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"owned synthetic recording")
    source_sha256 = file_sha256(video)
    analysis = AnalysisResult.model_validate_json(VALID_OUTPUT)
    policy = route_analysis(analysis, duration_seconds=10.0, source_sha256=source_sha256)
    result = policy.results[0]
    ledger = RunLedger.create(
        tmp_path / "output",
        source_sha256=source_sha256,
        fingerprint="a" * 64,
        fingerprint_inputs={"source": source_sha256},
    )
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "prompt.md").write_text("frozen prompt")
    (fixture / "project-context.md").write_text("frozen context")
    ground_truth = fixture / "ground-truth.json"
    ground_truth.write_text(
        json.dumps(
            {
                "fixture_version": "test-v1",
                "source_path": video.name,
                "source_sha256": source_sha256,
                "duration_seconds": 10.0,
                "cases": [
                    {
                        "case_id": "A",
                        "label": "one expected result",
                        "authored_window": {"start_seconds": 1.0, "end_seconds": 3.0},
                        "required": {
                            "topic_key": result.topic_key,
                            "type": result.type,
                            "intent": result.intent,
                            "route": expected_route,
                            "reason_code": result.reason_code,
                            "approval_eligible": result.approval_eligible,
                            "visually_inferred": result.visually_inferred,
                        },
                        "forbidden": {"routes": []},
                        "evidence_frame_required": False,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(
        "main.triage_recording",
        lambda *args, **kwargs: (
            VideoInfo(path=video, duration_seconds=10.0),
            VerifiedAnalysis(analysis=analysis, processing_pair_count=1),
            policy,
            ledger,
        ),
    )

    exit_code = main(["analyze", str(video), "--ground-truth", str(ground_truth)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == expected_exit_code
    assert output["status"] == expected_status
    assert output["score"]["passed"] is (expected_exit_code == 0)
