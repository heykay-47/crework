import json
from pathlib import Path

import pytest

from feedback_triage.input_video import InvalidInput, probe_video
from feedback_triage.models import EvidenceSpan


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


@pytest.mark.parametrize(
    ("start", "end", "keyframe"),
    [(-1.0, 1.0, None), (2.0, 1.0, None), (1.0, 2.0, 3.0)],
)
def test_evidence_timestamps_must_form_a_valid_span(start: float, end: float, keyframe: float | None) -> None:
    with pytest.raises(ValueError):
        EvidenceSpan(
            start_seconds=start,
            end_seconds=end,
            keyframe_seconds=keyframe,
            client_quote="evidence",
            visual_observation=None,
        )
