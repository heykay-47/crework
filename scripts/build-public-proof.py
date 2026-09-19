"""Build the checked-in, sanitized acceptance proof used by SUBMISSION.md.

The source of truth remains the proof-package builder.  This script only
creates public presentation media from the owned synthetic fixture, seeds the
deterministic acceptance harness used by the tests, and asks the read-only
builder to freeze the result.  It never calls Gemini or GitHub.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feedback_triage.acceptance import AcceptanceStore, ManualBaseline
from feedback_triage.evaluation import load_ground_truth
from feedback_triage.proof import build_proof_package
from tests.test_proof import _seed


FIXTURE = ROOT / "fixtures" / "canonical" / "feedback-recording.mp4"
GROUND_TRUTH = ROOT / "fixtures" / "canonical" / "ground-truth.json"
PUBLIC_DIR = ROOT / "docs" / "submission"
BUNDLE_DIR = PUBLIC_DIR / "proof-bundle"
FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/local/share/fonts/DejaVuSans.ttf"),
)


CARD_TEXT: dict[str, str] = {
    "capability-date": (
        "CAPABILITY / RELEASE\n"
        "Google: Agentic Video Understanding\n"
        "Official announcement: 2026-09-01\n"
        "Gemini Interactions + processing=agentic\n"
        "Project metrics below are not Google benchmarks."
    ),
    "fixture-manifest": (
        "FIXTURE MANIFEST\n"
        "canonical-six-case-v3\n"
        "synthetic MP4 | 360.026667 seconds\n"
        "SHA-256 f0b72e2f45e33166616e18293b1326be5fd8d1d8635a4e6a3770b516e9773fdc\n"
        "Cases A-F | one fixture | B/D duplicate identity\n"
        "Limitation: no live customer data."
    ),
    "request-trust": (
        "REQUEST + TRUST GATE\n"
        "stream=True | store=True\n"
        "persist interaction.created ID\n"
        "retrieve stored interaction\n"
        "completed + exact processing/result pairing\n"
        "Pydantic schema pass\n"
        "Failure means no observations, review, or write"
    ),
    "three-run-summary": (
        "ACCEPTANCE CYCLE\n"
        "1 analyze\n"
        "2 analyze --reanalyze\n"
        "3 run --reanalyze\n"
        "Deterministic harness: 3/3 qualifying analyses\n"
        "upload 1s | analysis 2s | wall 3s\n"
        "Gemini usage per attempt: 18 total tokens\n"
        "Stored-stream retrieval is verified; no watcher"
    ),
    "six-routes": (
        "EXPECTED = OBSERVED\n"
        "A candidate | B candidate | C clarification\n"
        "D candidate (deduped with B) | E withheld | F manual review\n"
        "All six cases match in each of three analyses\n"
        "Questions and visual-only results do not become actions"
    ),
    "evidence-frames": (
        "EVIDENCE FRAMES\n"
        "A/B/F actionable observations are framed before approval\n"
        "B: mobile navbar/logo overlap\n"
        "F: contact submit anomaly\n"
        "Frame paths and timestamps are frozen in the bundle"
    ),
    "manual-review-approval": (
        "MANUAL REVIEW + APPROVAL\n"
        "F stays Manual Review: visual-only, no client request\n"
        "Candidate writes require an immutable Approval\n"
        "No approval means no GitHub call\n"
        "Three adapter Issue Records follow approval"
    ),
    "final-issues": (
        "FINAL ISSUE RECORDS\n"
        "Adapter destination: demo/feedback\n"
        "Issue Records: 1, 2, 3\n"
        "URLs are example.test placeholders\n"
        "Not live GitHub Issues; this public package makes no external write\n"
        "A live destination remains deployment configuration"
    ),
    "sanitized-run-ledger": (
        "SANITIZED RUN LEDGER\n"
        "Three verified attempts + two rejected attempts\n"
        "Measurements | usage | policy | frames | approvals | writes\n"
        "No API bodies, credentials, private paths, or interaction IDs\n"
        "Atomic per-source records and failure provenance"
    ),
}


def _run(*args: str) -> None:
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def _font_path() -> Path:
    override = Path(os.environ["CREWORK_PROOF_FONT"]) if "CREWORK_PROOF_FONT" in os.environ else None
    for candidate in (override, *FONT_CANDIDATES):
        if candidate is not None and candidate.is_file():
            return candidate
    raise SystemExit("missing DejaVu Sans font; set CREWORK_PROOF_FONT to a font file")


def _make_card(path: Path, text: str, *, background: str = "0d1b2a") -> None:
    line_files: list[Path] = []
    for index, line in enumerate(text.splitlines()):
        line_file = path.with_name(f"{path.stem}-line-{index}.txt")
        line_file.write_text(line, encoding="utf-8")
        line_files.append(line_file)
    font_size = 26 if max((len(line) for line in text.splitlines()), default=0) > 70 else 34
    line_step = font_size + 18
    font = _font_path()
    draw_lines = ",".join(
        (
            f"drawtext=fontfile={font}:textfile={line_file}:fontcolor=0xe8f1f2:"
            f"fontsize={font_size}:x=70:y={65 + index * line_step}"
        )
        for index, line_file in enumerate(line_files)
    )
    _run(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x{background}:s=1280x720:d=1",
        "-vf",
        draw_lines,
        "-frames:v",
        "1",
        str(path),
    )


def _extract_frame(path: Path, timestamp: float) -> None:
    _run(
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp:.1f}",
        "-i",
        str(FIXTURE),
        "-frames:v",
        "1",
        "-vf",
        "scale=1280:-1",
        str(path),
    )


def _make_gif(cards: list[Path], output: Path, work_dir: Path) -> None:
    concat = work_dir / "gif-input.txt"
    concat.write_text(
        "\n".join(f"file '{card}'\nduration 6" for card in cards)
        + f"\nfile '{cards[-1]}'\n",
        encoding="utf-8",
    )
    _run(
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-t",
        "24",
        "-vf",
        (
            "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];"
            "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=sierra2_4a"
        ),
        str(output),
    )


def main() -> None:
    if not FIXTURE.is_file():
        raise SystemExit(f"missing canonical fixture: {FIXTURE}")
    _font_path()

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)

    with tempfile.TemporaryDirectory(prefix="crework-public-proof-") as temporary:
        temporary_dir = Path(temporary)
        acceptance, ledger, images, gif, timeline, incidents = _seed(
            temporary_dir,
            fingerprint_inputs={
                "request_trust": (
                    "stream=True; store=True; persist interaction.created; "
                    "retrieve exact ID; require completed and matched processing pairs"
                )
            },
        )
        acceptance_store = AcceptanceStore(
            temporary_dir / "output" / acceptance.source_sha256 / "acceptance.json",
            source_sha256=acceptance.source_sha256,
            fingerprint=acceptance.fingerprint,
        )
        acceptance_store.set_manual_baseline(
            ManualBaseline(
                source_sha256=acceptance.source_sha256,
                recording_label="canonical-six-case-v3",
                equivalent_issue_count=3,
                equivalent_issue_numbers=(1, 2, 3),
                watch_seconds=360.0,
                issue_writing_seconds=120.0,
                active_human_seconds=480.0,
                measured_at="2026-09-19T00:00:00+00:00",
                notes="Deterministic acceptance-harness baseline; no live GitHub destination.",
            )
        )
        acceptance = acceptance_store.record

        for label, path in images.items():
            _make_card(path, CARD_TEXT[label])

        frame_times = {
            "cand_2c8c489a2f60f030": 20.5,
            "cand_481753781d347a60": 80.5,
            "cand_191f3b4d6d46effb": 320.5,
        }
        # _seed records placeholder Evidence Frame bytes at stable paths. Keep
        # those paths and replace the bytes with real fixture frames so the
        # public package cannot silently pass with header-only images.
        for frame in ledger.evidence_frames_for_attempt(timeline.final_attempt_id):
            if frame.candidate_id not in frame_times:
                raise RuntimeError(f"unexpected seeded Evidence Frame: {frame.candidate_id}")
            _extract_frame(Path(frame.path), frame_times[frame.candidate_id])

        _make_gif(
            [images["three-run-summary"], images["six-routes"], images["manual-review-approval"], images["final-issues"]],
            gif,
            temporary_dir,
        )

        build_proof_package(
            acceptance,
            ledger,
            load_ground_truth(GROUND_TRUTH),
            bundle_dir=BUNDLE_DIR,
            images=images,
            gif=gif,
            gif_timeline=timeline,
            incidents=incidents,
        )
        shutil.copyfile(BUNDLE_DIR / "media" / "third-run.gif", PUBLIC_DIR / "third-run.gif")

    print(f"wrote {BUNDLE_DIR}")


if __name__ == "__main__":
    main()
