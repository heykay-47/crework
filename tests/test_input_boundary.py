import json
from pathlib import Path

import pytest

from feedback_triage.input_video import InvalidInput, probe_video


def test_probe_rejects_unprobeable_input(tmp_path: Path) -> None:
    video = tmp_path / "not-a-video.mp4"
    video.write_text("not video")

    with pytest.raises(InvalidInput, match="ffprobe could not read"):
        probe_video(video)


def test_probe_returns_positive_duration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"owned synthetic recording")

    class Result:
        returncode = 0
        stdout = json.dumps({"format": {"duration": "12.5"}})
        stderr = ""

    monkeypatch.setattr("feedback_triage.input_video.subprocess.run", lambda *args, **kwargs: Result())

    assert probe_video(video).duration_seconds == 12.5
