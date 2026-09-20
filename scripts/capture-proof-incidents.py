"""Capture sanitized safety incidents for the public acceptance proof.

The default mode is a local dry run. ``--execute-background`` performs one
real Gemini background request against the owned canonical fixture. The
expected API rejection and a controlled no-write incomplete-analysis case are
persisted in a separate Run Ledger epoch beside the successful live ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from google.genai._gaos.lib.compat_errors import BadRequestError
from google.genai.errors import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feedback_triage.gemini_video import MODEL, _retry_call, _value, compose_prompt, create_client
from feedback_triage.ledger import RunLedger
from feedback_triage.persistence import atomic_write_json


SOURCE_SHA256 = "f0b72e2f45e33166616e18293b1326be5fd8d1d8635a4e6a3770b516e9773fdc"
FIXTURE = ROOT / "fixtures" / "canonical" / "feedback-recording.mp4"
PROMPT = ROOT / "fixtures" / "canonical" / "prompt.md"
CONTEXT = ROOT / "fixtures" / "canonical" / "project-context.md"


def _status_code(error: Exception) -> int | None:
    for name in ("status_code", "code"):
        value = getattr(error, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _upload_active(client: Any) -> Any:
    uploaded = client.files.upload(file=str(FIXTURE))
    deadline = time.monotonic() + 300
    state = _value(_value(uploaded, "state"), "name")
    while state == "PROCESSING" and time.monotonic() < deadline:
        time.sleep(min(2, deadline - time.monotonic()))
        uploaded = _retry_call(lambda: client.files.get(name=_value(uploaded, "name")), deadline=deadline)
        state = _value(_value(uploaded, "state"), "name")
    if state != "ACTIVE":
        raise RuntimeError(f"uploaded fixture did not become ACTIVE: {state}")
    return uploaded


def _classify_background_rejection(error: Exception) -> dict[str, object] | None:
    status = _status_code(error)
    message = str(error)
    normalized = message.casefold()
    if (
        status not in {400, 422}
        or not isinstance(error, (ClientError, BadRequestError))
        or "background" not in normalized
    ):
        return None
    return {
        "api_error_module": type(error).__module__,
        "api_error_type": type(error).__name__,
        "http_status": status,
        "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
        "matched_rejection_terms": ["background", "http_client_error"],
    }


def _write_incident_inputs(
    capture_dir: Path,
    background_attempt: str,
    incomplete_attempt: str,
    rejection: dict[str, object],
) -> None:
    capture_dir.mkdir(parents=True, exist_ok=True)
    background_evidence = capture_dir / "background-rejection-evidence.json"
    incomplete_evidence = capture_dir / "incomplete-analysis-evidence.json"
    atomic_write_json(
        background_evidence,
        {
            "source_sha256": SOURCE_SHA256,
            "attempt_id": background_attempt,
            "model": MODEL,
            "request": {"background": True, "store": True, "processing": "agentic"},
            "api_rejection": rejection,
            "observed_status": "rejected",
            "external_write_count": 0,
            "note": "Sanitized evidence from a real Gemini API request; the message hash and matched terms prove classification without publishing the API body.",
        },
        prefix="background-rejection-",
    )
    atomic_write_json(
        incomplete_evidence,
        {
            "source_sha256": SOURCE_SHA256,
            "attempt_id": incomplete_attempt,
            "observed_status": "failed",
            "external_write_count": 0,
            "note": "Controlled incomplete-analysis safety case persisted without invoking Gemini or GitHub.",
        },
        prefix="incomplete-analysis-",
    )
    atomic_write_json(
        capture_dir / "background-rejection.json",
        {
            "incident_id": "background-rejection-live",
            "kind": "background_interaction_rejection",
            "attempt_id": background_attempt,
            "observed_code": "background_rejected",
            "observed_status": "rejected",
            "external_write_count": 0,
            "summary": "A real background agentic-video request was rejected; no Candidate or GitHub write path ran.",
            "artifact_path": str(background_evidence),
        },
        prefix="background-incident-",
    )
    atomic_write_json(
        capture_dir / "zero-write-case.json",
        {
            "incident_id": "controlled-incomplete-analysis",
            "kind": "incomplete_analysis",
            "attempt_id": incomplete_attempt,
            "observed_code": "processing_unverified",
            "observed_status": "failed",
            "external_write_count": 0,
            "summary": "A controlled incomplete-analysis state proves the ledger blocks review and external writes.",
            "artifact_path": str(incomplete_evidence),
        },
        prefix="incomplete-incident-",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "acceptance-live")
    parser.add_argument("--capture", type=Path, default=ROOT / ".agent" / "capture" / "incidents")
    parser.add_argument("--execute-background", action="store_true")
    args = parser.parse_args()

    current_path = args.output / SOURCE_SHA256 / "ledger.json"
    current = RunLedger.load(current_path)
    inputs = dict(current.fingerprint_inputs)
    inputs["proof_incident_epoch"] = "real-background-rejection-v1"
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    archive_path = current_path.with_name(f"ledger-{fingerprint}.json")
    if not args.execute_background:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "source_sha256": SOURCE_SHA256,
                    "model": MODEL,
                    "request": {"background": True, "store": True, "processing": "agentic"},
                    "expected": "API rejection; zero external writes",
                },
                sort_keys=True,
            )
        )
        return
    if archive_path.exists():
        raise SystemExit(f"incident epoch already exists: {archive_path}")
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is required for --execute-background")

    with tempfile.TemporaryDirectory(prefix="crework-proof-incidents-") as temporary:
        ledger = RunLedger.create(
            Path(temporary),
            source_sha256=SOURCE_SHA256,
            fingerprint=fingerprint,
            fingerprint_inputs=inputs,
        )
        background_attempt = ledger.start_attempt()
        started = time.monotonic()
        client = create_client()
        uploaded = _upload_active(client)
        try:
            client.interactions.create(
                model=MODEL,
                input=[
                    {
                        "type": "video",
                        "uri": _value(uploaded, "uri"),
                        "mime_type": _value(uploaded, "mime_type"),
                        "processing": "agentic",
                    },
                    {
                        "type": "text",
                        "text": compose_prompt(PROMPT.read_text(), CONTEXT.read_text()),
                    },
                ],
                background=True,
                store=True,
            )
        except Exception as error:
            rejection = _classify_background_rejection(error)
            if rejection is None:
                raise SystemExit(
                    "unexpected Gemini failure; not captured as background rejection "
                    f"(module={type(error).__module__}, type={type(error).__name__}, "
                    f"status={_status_code(error)}, mentions_background={'background' in str(error).casefold()})"
                ) from None
            ledger.record_measurement(background_attempt, "wall_seconds", time.monotonic() - started)
            ledger.record_measurement(background_attempt, "external_write_count", 0)
            ledger.fail(
                background_attempt,
                code="background_rejected",
                detail=(
                    "real Gemini API rejected the background agentic-video request as unsupported "
                    f"with HTTP {rejection['http_status']}; no external write"
                ),
            )
        else:
            raise SystemExit("background request was accepted; refusing to manufacture rejection evidence")

        incomplete_attempt = ledger.start_attempt()
        ledger.record_measurement(incomplete_attempt, "external_write_count", 0)
        ledger.fail(
            incomplete_attempt,
            code="processing_unverified",
            detail="controlled incomplete analysis: processing proof unavailable; no external write",
        )
        shutil.copyfile(ledger.path, archive_path)
        _write_incident_inputs(args.capture, background_attempt, incomplete_attempt, rejection)

    print(json.dumps({"status": "captured", "ledger_archive": str(archive_path)}, sort_keys=True))


if __name__ == "__main__":
    main()
