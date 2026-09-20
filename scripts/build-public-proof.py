"""Build the checked-in public proof from the completed live acceptance run.

This read-only presentation step consumes persisted acceptance data, the real
asciinema recording, reviewer-captured GitHub screenshot, and sanitized safety
incidents. It never calls Gemini or GitHub.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feedback_triage.acceptance import AcceptanceRecord
from feedback_triage.evaluation import load_ground_truth
from feedback_triage.ledger import RunLedger
from feedback_triage.proof import GifTimeline, ProofIncident, build_proof_package


SOURCE_SHA256 = "f0b72e2f45e33166616e18293b1326be5fd8d1d8635a4e6a3770b516e9773fdc"
GROUND_TRUTH = ROOT / "fixtures" / "canonical" / "ground-truth.json"
LIVE_DIR = ROOT / "output" / "acceptance-live" / SOURCE_SHA256
ACCEPTANCE_PATH = LIVE_DIR / "acceptance.json"
LEDGER_PATH = LIVE_DIR / "ledger.json"
CAST_PATH = ROOT / ".agent" / "capture" / "third-run.cast"
GITHUB_SCREENSHOT = ROOT / ".agent" / "capture" / "images" / "08-final-issues.png"
INCIDENT_DIR = ROOT / ".agent" / "capture" / "incidents"
AGG = ROOT / ".agent" / "bin" / "agg"
PUBLIC_DIR = ROOT / "docs" / "submission"
BUNDLE_DIR = PUBLIC_DIR / "proof-bundle"
FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/local/share/fonts/DejaVuSans.ttf"),
)


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
    font_size = 25 if max((len(line) for line in text.splitlines()), default=0) > 68 else 32
    line_step = font_size + 18
    font = _font_path()
    draw_lines = ",".join(
        f"drawtext=fontfile={font}:textfile={line}:fontcolor=0xe8f1f2:fontsize={font_size}:x=60:y={55 + index * line_step}"
        for index, line in enumerate(line_files)
    )
    _run(
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x{background}:s=1280x720:d=1",
        "-vf", draw_lines, "-frames:v", "1", str(path),
    )


def _make_evidence_collage(path: Path, frame_paths: list[Path]) -> None:
    if len(frame_paths) != 3:
        raise RuntimeError("expected exactly three final Evidence Frames")
    _run(
        "ffmpeg", "-y",
        "-i", str(frame_paths[0]), "-i", str(frame_paths[1]), "-i", str(frame_paths[2]),
        "-filter_complex",
        (
            "[0:v]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2[a];"
            "[1:v]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2[b];"
            "[2:v]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2[c];"
            "color=c=0x0d1b2a:s=640x360[d];[a][b][c][d]xstack=inputs=4:layout=0_0|640_0|0_360|640_360[out]"
        ),
        "-map", "[out]", "-frames:v", "1", str(path),
    )


def _render_real_gif(output: Path) -> None:
    raw = output.with_name("third-run-raw.gif")
    _run(
        str(AGG), "--idle-time-limit", "5", "--last-frame-duration", "3", "--fps-cap", "12",
        "--font-size", "14", "--cols", "130", "--rows", "42", "--no-loop", str(CAST_PATH), str(raw),
    )
    intro = output.with_name("third-run-command.png")
    _make_card(intro, (
        "RECORDED THIRD RUN\n"
        "$ docker run [documented mounts/env] client-feedback-triage run\n"
        "  /app/fixtures/canonical/feedback-recording.mp4\n"
        "  --output /output --reanalyze\n"
        "  --repository heykay-47/crework-feedback-demo\n"
        "  --manual-baseline /manual-baseline.json\n"
        "Edited only to remove waits and add truthful labels; terminal output is authentic."
    ))
    wait_label = output.with_name("third-run-removed-waits.txt")
    wait_label.write_text(
        "removed waits - analysis: 70.06s | review: 216.02s | manual confirmation: 27.99s",
        encoding="utf-8",
    )
    body_filters = (
        "drawbox=x=15:y=15:w=1200:h=48:color=black@0.78:t=fill,"
        f"drawtext=fontfile={_font_path()}:textfile={wait_label}:fontcolor=white:fontsize=24:x=30:y=26"
    )
    _run(
        "ffmpeg", "-y", "-loop", "1", "-framerate", "12", "-t", "2", "-i", str(intro),
        "-ignore_loop", "1", "-i", str(raw),
        "-filter_complex",
        (
            "[0:v]fps=12,scale=1280:720,format=rgb24,setpts=PTS-STARTPTS[intro];"
            "[1:v]fps=12,scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,setpts=PTS-STARTPTS,"
            f"{body_filters}[body];"
            "[intro][body]concat=n=2:v=1:a=0,split[palette_input][video_input];"
            "[palette_input]palettegen=max_colors=128[palette];"
            "[video_input][palette]paletteuse=dither=bayer[out]"
        ),
        "-map", "[out]", "-loop", "-1", str(output),
    )


def _extract_gif_frame(gif: Path, output: Path, seconds: float) -> None:
    _run("ffmpeg", "-y", "-ss", f"{seconds:.2f}", "-i", str(gif), "-frames:v", "1", str(output))


def _attempt_text(attempt: dict[str, object]) -> tuple[float, float, float, int]:
    measurements = attempt["measurements"]
    usage = attempt["gemini_usage"]
    assert isinstance(measurements, dict) and isinstance(usage, dict)
    return (
        float(measurements["upload_seconds"]),
        float(measurements["analysis_seconds"]),
        float(measurements["wall_clock_seconds"]),
        int(usage["total_tokens"]),
    )


def main() -> None:
    required = (ACCEPTANCE_PATH, LEDGER_PATH, CAST_PATH, GITHUB_SCREENSHOT, AGG)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"missing live proof inputs: {missing}")

    acceptance = AcceptanceRecord.model_validate_json(ACCEPTANCE_PATH.read_text())
    ledger = RunLedger.load(LEDGER_PATH)
    final_attempt_id = acceptance.qualified_attempt_ids[-1]
    attempts = {value["attempt_id"]: value for value in ledger.attempts}
    qualified = [attempts[attempt_id] for attempt_id in acceptance.qualified_attempt_ids]
    final_attempt = qualified[-1]
    policy = final_attempt["policy_result"]
    assert isinstance(policy, dict)
    routes = policy["results"]
    assert isinstance(routes, list)
    route_counts: dict[str, int] = {}
    for route in routes:
        assert isinstance(route, dict)
        label = str(route["route"])
        route_counts[label] = route_counts.get(label, 0) + 1

    with tempfile.TemporaryDirectory(prefix="crework-public-proof-") as temporary:
        work = Path(temporary)
        images = {label: work / f"{index:02d}-{label}.png" for index, label in enumerate(
            (
                "capability-date", "fixture-manifest", "request-trust", "three-run-summary", "six-routes",
                "evidence-frames", "manual-review-approval", "final-issues", "sanitized-run-ledger",
            ), start=1
        )}

        _make_card(images["capability-date"], (
            "CAPABILITY / RELEASE SOURCE\nGoogle: Agentic Video Understanding\n"
            "Official announcement: 2026-09-01\nGemini Interactions + processing=agentic\n"
            "Project measurements below are not Google benchmarks."
        ))
        _make_card(images["fixture-manifest"], (
            "FROZEN FIXTURE MANIFEST\ncanonical-six-case-v3 | synthetic owned MP4\n"
            "Duration 360.026667 seconds | Cases A-F\n"
            f"SHA-256 {SOURCE_SHA256}\nOne fixture; B/D share one stable Candidate identity."
        ))
        _make_card(images["request-trust"], (
            "LIVE REQUEST + TRUST GATE\nstream=True | store=True | processing=agentic\n"
            "Each interaction.created ID persisted before later events\n"
            "All 3 retrieved interactions completed with exact processing pairs\n"
            "Schema + policy pass before review; failure means zero writes."
        ))
        run_lines = ["THREE LIVE GEMINI ANALYSES"]
        for index, attempt in enumerate(qualified, start=1):
            upload, analysis, wall, tokens = _attempt_text(attempt)
            run_lines.append(
                f"{index}. {attempt['attempt_id'][:8]} | upload {upload:.2f}s | analysis {analysis:.2f}s | wall {wall:.2f}s"
            )
            run_lines.append(f"   total tokens: {tokens:,}")
        run_lines.extend(("All 3 matched Cases A-F.", "Third run created 3 verified public demo Issues."))
        _make_card(images["three-run-summary"], "\n".join(run_lines))
        _make_card(images["six-routes"], (
            "FINAL LIVE ROUTES — EXPECTED = OBSERVED\n"
            f"candidate {route_counts.get('candidate', 0)} | manual_review {route_counts.get('manual_review', 0)} | "
            f"clarification {route_counts.get('clarification_request', 0)} | withheld {route_counts.get('withheld_result', 0)}\n"
            "Cases A-F matched; B/D deduped to one Candidate.\n"
            "Clarification and Withheld Results never entered review or writes."
        ))

        frame_paths = []
        for frame in ledger.evidence_frames_for_attempt(final_attempt_id):
            if frame.status != "extracted" or frame.path is None:
                raise RuntimeError(f"expected an extracted Evidence Frame for {frame.candidate_id}")
            frame_paths.append(ROOT / "output" / "acceptance-live" / Path(frame.path).relative_to("/output"))
        _make_evidence_collage(images["evidence-frames"], frame_paths)
        gif = work / "third-run.gif"
        _render_real_gif(gif)
        _extract_gif_frame(gif, images["manual-review-approval"], 13.0)
        shutil.copyfile(GITHUB_SCREENSHOT, images["final-issues"])
        _make_card(images["sanitized-run-ledger"], (
            "SANITIZED LIVE RUN LEDGER\n3 verified Gemini attempts | processing pairs 4 / 2 / 2\n"
            "Final active human 246.37s | external writes 3\n"
            "Real Issue Records: heykay-47/crework-feedback-demo #4, #5, #6\n"
            "Safety epoch: real background rejection + controlled incomplete case\n"
            "No credentials, API bodies, private paths, or interaction IDs."
        ))

        timeline = GifTimeline.model_validate(
            {
                "final_attempt_id": final_attempt_id,
                "events": (
                    {"label": "third-run-command", "attempt_id": final_attempt_id, "elapsed_seconds": 1.0, "description": "The recorded run command is visible before authentic terminal output."},
                    {"label": "verified-analysis", "attempt_id": final_attempt_id, "elapsed_seconds": 7.0, "description": "Verified analysis produced actionable review prompts."},
                    {"label": "routes-and-review", "attempt_id": final_attempt_id, "elapsed_seconds": 13.7, "description": "Candidate and Manual Review decisions were recorded."},
                    {"label": "verified-issues", "attempt_id": final_attempt_id, "elapsed_seconds": 23.1, "description": "The recorded command ended with acceptance_passed and three Issue Records."},
                ),
                "removed_waits": (
                    {"label": "live Gemini analysis before first prompt", "elapsed_seconds": 70.063},
                    {"label": "operator review before first approval", "elapsed_seconds": 216.020},
                    {"label": "operator review before manual confirmation", "elapsed_seconds": 27.987},
                ),
                "recorded_run": True,
                "watcher_used": False,
                "replay_fabricated_output": False,
            }
        )
        incidents = tuple(
            ProofIncident.model_validate_json((INCIDENT_DIR / name).read_text())
            for name in ("background-rejection.json", "zero-write-case.json")
        )

        # Container runs persist Evidence Frame paths under /output. Package
        # from a temporary ledger view whose paths resolve on this host while
        # preserving every persisted ID, hash, and behavior fingerprint.
        package_ledger_dir = work / "ledger"
        package_ledger_dir.mkdir()
        package_payload = json.loads(LEDGER_PATH.read_text())
        for attempt in package_payload["attempts"]:
            for frame in attempt.get("evidence_frames", []):
                frame["path"] = str(
                    ROOT / "output" / "acceptance-live" / Path(frame["path"]).relative_to("/output")
                )
        package_ledger_path = package_ledger_dir / "ledger.json"
        package_ledger_path.write_text(json.dumps(package_payload, indent=2, sort_keys=True) + "\n")
        for archive in LEDGER_PATH.parent.glob("ledger-*.json"):
            shutil.copyfile(archive, package_ledger_dir / archive.name)
        package_ledger = RunLedger.load(package_ledger_path)

        if BUNDLE_DIR.exists():
            shutil.rmtree(BUNDLE_DIR)
        build_proof_package(
            acceptance,
            package_ledger,
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
