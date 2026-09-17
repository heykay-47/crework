import subprocess
from pathlib import Path

import pytest

from feedback_triage.evidence import extract_evidence_frame
from feedback_triage.models import EvidenceSpan
from feedback_triage.policy import select_evidence_frame_timestamp


def span(
    start: float,
    end: float,
    *,
    keyframe: float | None = None,
    quote: str | None = None,
    visual: str | None = None,
) -> EvidenceSpan:
    return EvidenceSpan(
        start_seconds=start,
        end_seconds=end,
        keyframe_seconds=keyframe,
        client_quote=quote or (None if visual else "client wording"),
        visual_observation=visual,
    )


def test_timestamp_selection_prefers_earliest_visual_keyframe() -> None:
    evidence = [
        span(20.0, 30.0, keyframe=24.0, visual="later visual state"),
        span(10.0, 12.0, keyframe=11.0, visual="earlier visual state"),
        span(1.0, 2.0, visual="visual state without supplied keyframe"),
    ]

    assert select_evidence_frame_timestamp(evidence) == 11.0


def test_timestamp_selection_uses_earliest_visual_midpoint_without_keyframes() -> None:
    evidence = [
        span(20.0, 30.0, visual="later visual state"),
        span(4.0, 8.0, visual="earlier visual state"),
    ]

    assert select_evidence_frame_timestamp(evidence) == 6.0


def test_timestamp_selection_uses_earliest_keyframe_when_no_visual_evidence() -> None:
    evidence = [
        span(20.0, 30.0, keyframe=24.0),
        span(10.0, 12.0, keyframe=11.0),
    ]

    assert select_evidence_frame_timestamp(evidence) == 11.0


def test_timestamp_selection_falls_back_to_earliest_interval_midpoint() -> None:
    evidence = [span(20.0, 30.0), span(4.0, 8.0)]

    assert select_evidence_frame_timestamp(evidence) == 6.0


def test_timestamp_selection_treats_whitespace_visual_text_as_absent() -> None:
    evidence = [
        span(0.0, 100.0, quote="client wording", visual="   "),
        span(4.0, 6.0, keyframe=5.0),
    ]

    assert select_evidence_frame_timestamp(evidence) == 5.0


def test_timestamp_selection_accepts_zero_keyframe() -> None:
    assert select_evidence_frame_timestamp([span(0.0, 2.0, keyframe=0.0, visual="first frame")]) == 0.0


def test_timestamp_selection_rejects_empty_evidence() -> None:
    with pytest.raises(ValueError, match="at least one Evidence Span"):
        select_evidence_frame_timestamp([])


def test_ffmpeg_extraction_records_a_successful_frame(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "feedback recording.mp4"
    video.write_bytes(b"video")
    frame = tmp_path / "frames" / "candidate.png"
    commands: list[list[str]] = []

    def run(command: list[str], **_: object) -> object:
        commands.append(command)
        frame.parent.mkdir(exist_ok=True)
        frame.write_bytes(b"png")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("feedback_triage.evidence.subprocess.run", run)

    result = extract_evidence_frame(
        video,
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=12.5,
        output_path=frame,
    )

    assert result.status == "extracted"
    assert result.path == str(frame)
    assert result.error is None
    assert commands == [
        [
            "ffmpeg",
            "-y",
            "-ss",
            "12.5",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(frame),
        ]
    ]


def test_ffmpeg_failure_is_recorded_without_raising(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"video")
    frame = tmp_path / "candidate.png"

    def run(*_: object, **__: object) -> object:
        return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "decoder failed"})()

    monkeypatch.setattr("feedback_triage.evidence.subprocess.run", run)

    result = extract_evidence_frame(
        video,
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=12.5,
        output_path=frame,
    )

    assert result.status == "failed"
    assert result.path is None
    assert result.error is not None
    assert "decoder failed" in result.error
    assert not frame.exists()


def test_ffmpeg_success_without_a_frame_is_recorded_as_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"video")
    frame = tmp_path / "candidate.png"

    monkeypatch.setattr(
        "feedback_triage.evidence.subprocess.run",
        lambda *_args, **_kwargs: type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    result = extract_evidence_frame(
        video,
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=12.5,
        output_path=frame,
    )

    assert result.status == "failed"
    assert result.error is not None
    assert "Evidence Frame" in result.error


def test_missing_ffmpeg_is_recorded_as_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"video")

    def run(*_: object, **__: object) -> object:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr("feedback_triage.evidence.subprocess.run", run)

    result = extract_evidence_frame(
        video,
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=12.5,
        output_path=tmp_path / "candidate.png",
    )

    assert result.status == "failed"
    assert result.error is not None
    assert "unavailable" in result.error


def test_ffmpeg_timeout_is_recorded_as_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"video")

    def run(*_: object, **__: object) -> object:
        raise subprocess.TimeoutExpired("ffmpeg", 30, stderr=b"still running")

    monkeypatch.setattr("feedback_triage.evidence.subprocess.run", run)

    result = extract_evidence_frame(
        video,
        candidate_id="cand_aaaaaaaaaaaaaaaa",
        timestamp_seconds=12.5,
        output_path=tmp_path / "candidate.png",
    )

    assert result.status == "failed"
    assert result.error is not None
    assert "timed out" in result.error
    assert "still running" in result.error
