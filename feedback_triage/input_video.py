import json
import math
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvalidInput(ValueError):
    """The Feedback Recording cannot safely enter analysis."""


class VideoInfo(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: Path
    duration_seconds: float = Field(gt=0)


def probe_video(path: Path) -> VideoInfo:
    if not path.is_file():
        raise InvalidInput(f"Feedback Recording does not exist: {path}")
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise InvalidInput(f"ffprobe could not read Feedback Recording: {completed.stderr.strip()}")
    try:
        payload: Any = json.loads(completed.stdout)
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise InvalidInput("ffprobe did not return a duration") from error
    if not math.isfinite(duration) or duration <= 0:
        raise InvalidInput("Feedback Recording duration must be finite and positive")
    return VideoInfo(path=path, duration_seconds=duration)
