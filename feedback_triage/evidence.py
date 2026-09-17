import math
import subprocess
from pathlib import Path

from feedback_triage.models import EvidenceFrameRecord

FFMPEG_TIMEOUT_SECONDS = 30.0


def _subprocess_detail(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return value.strip()


def _failed_frame_record(candidate_id: str, timestamp_seconds: float, error: str) -> EvidenceFrameRecord:
    return EvidenceFrameRecord(
        candidate_id=candidate_id,
        timestamp_seconds=timestamp_seconds,
        status="failed",
        error=error,
    )


def extract_evidence_frame(
    video: Path,
    *,
    candidate_id: str,
    timestamp_seconds: float,
    output_path: Path,
) -> EvidenceFrameRecord:
    """Extract one still image, returning a record for success or failure.

    FFmpeg failures are represented in the returned record rather than raised;
    callers can persist the outcome and continue routing other Candidates.
    """
    if not math.isfinite(timestamp_seconds) or timestamp_seconds < 0:
        raise ValueError("Evidence Frame timestamp must be finite and non-negative")

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
    except OSError as error:
        return _failed_frame_record(
            candidate_id,
            timestamp_seconds,
            f"could not prepare Evidence Frame output: {error}",
        )

    command = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp_seconds),
        "-i",
        str(video),
        "-frames:v",
        "1",
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        return _failed_frame_record(candidate_id, timestamp_seconds, f"ffmpeg executable is unavailable: {error}")
    except subprocess.TimeoutExpired as error:
        return _failed_frame_record(
            candidate_id,
            timestamp_seconds,
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_SECONDS:g} seconds: {_subprocess_detail(error.stderr)}".rstrip(": "),
        )
    except OSError as error:
        return _failed_frame_record(candidate_id, timestamp_seconds, f"could not run ffmpeg: {error}")

    if completed.returncode != 0:
        stderr = _subprocess_detail(completed.stderr)
        detail = f"ffmpeg exited with status {completed.returncode}"
        if stderr:
            detail = f"{detail}: {stderr}"
        return _failed_frame_record(candidate_id, timestamp_seconds, detail)

    try:
        usable = output_path.is_file() and output_path.stat().st_size > 0
    except OSError as error:
        return _failed_frame_record(candidate_id, timestamp_seconds, f"could not inspect extracted frame: {error}")
    if not usable:
        return _failed_frame_record(candidate_id, timestamp_seconds, "ffmpeg completed without a usable Evidence Frame")

    return EvidenceFrameRecord(
        candidate_id=candidate_id,
        timestamp_seconds=timestamp_seconds,
        status="extracted",
        path=str(output_path),
    )
