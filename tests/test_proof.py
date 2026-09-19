from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import main as cli
from feedback_triage.acceptance import AcceptanceRecord, AcceptanceStore
from feedback_triage.approval import build_approval, marker_for
from feedback_triage.evaluation import load_ground_truth
from feedback_triage.fingerprint import fingerprint_inputs_digest
from feedback_triage.ledger import RunLedger
from feedback_triage.models import AnalysisResult, EvidenceFrameRecord, IssueRecord
from feedback_triage.proof import (
    GIF_EVENT_SEQUENCE,
    IMAGE_SEQUENCE,
    GifEvent,
    GifTimeline,
    RemovedWait,
    ProofIncident,
    ProofPackageError,
    _audit_public_bytes,
    build_proof_package,
)
from tests.test_acceptance_cli import _canonical_policy
from tests.test_completion_trust import VALID_OUTPUT


SOURCE = "f0b72e2f45e33166616e18293b1326be5fd8d1d8635a4e6a3770b516e9773fdc"
GROUND_TRUTH_PATH = Path(__file__).parents[1] / "fixtures" / "canonical" / "ground-truth.json"


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\nproof"


def _gif() -> bytes:
    # One valid 1x1 GIF frame with a 25-second graphics-control delay.
    return (
        b"GIF89a"
        b"\x01\x00\x01\x00\x80\x00\x00"
        b"\x00\x00\x00\xff\xff\xff"
        b"!\xf9\x04\x00\xc4\x09\x00\x00"
        b",\x00\x00\x00\x00\x01\x00\x01\x00\x00"
        b"\x02\x02D\x01\x00"
        b";"
    )


def _seed(
    tmp_path: Path,
    *,
    fingerprint_inputs: dict[str, str] | None = None,
) -> tuple[AcceptanceRecord, RunLedger, dict[str, Path], Path, GifTimeline, tuple[ProofIncident, ...]]:
    output = tmp_path / "output"
    inputs = {"source": SOURCE, "model": "gemini-3.5-flash-lite"}
    if fingerprint_inputs:
        inputs.update(fingerprint_inputs)
    fingerprint = fingerprint_inputs_digest(inputs)
    ledger = RunLedger.create(
        output,
        source_sha256=SOURCE,
        fingerprint=fingerprint,
        fingerprint_inputs=inputs,
    )
    acceptance_store = AcceptanceStore(
        output / SOURCE / "acceptance.json",
        source_sha256=SOURCE,
        fingerprint=fingerprint,
    )
    policy = _canonical_policy()
    analysis = AnalysisResult.model_validate_json(VALID_OUTPUT)
    attempt_ids: list[str] = []
    actionable = tuple(result for result in policy.results if result.route in {"candidate", "manual_review"})
    frame_paths: dict[str, Path] = {}
    rejected_attempt = ledger.start_attempt()
    ledger.fail(rejected_attempt, code="background_not_supported", detail="background request rejected")
    incomplete_attempt = ledger.start_attempt()
    ledger.fail(incomplete_attempt, code="processing_unverified", detail="analysis did not complete")
    uncertain_attempt = ledger.start_attempt()
    uncertain_approval = build_approval(SOURCE, actionable[0], "demo/feedback")
    uncertain_write_id = ledger.record_write_pending(uncertain_attempt, uncertain_approval)
    ledger.record_measurement(uncertain_attempt, "external_write_count", 0.0)
    ledger.record_write_state(
        uncertain_write_id,
        uncertain_attempt,
        uncertain_approval,
        "write_uncertain",
        detail="response status was unavailable; reconciliation required",
    )

    for step, reanalyze in (("analyze", False), ("analyze", True), ("run", True)):
        acceptance_store.begin(step, reanalyze=reanalyze)  # type: ignore[arg-type]
        attempt_id = ledger.start_attempt()
        attempt_ids.append(attempt_id)
        ledger.record_interaction_created(attempt_id, f"private-interaction-{len(attempt_ids)}")
        ledger.complete(attempt_id, analysis.model_dump(mode="json"), processing_pair_count=2)
        measurements = {
            "upload_seconds": 1.0,
            "analysis_seconds": 2.0,
            "wall_clock_seconds": 3.0,
            "semantic_score_passed": 1.0,
        }
        if step == "run":
            measurements.update(
                {
                    "frame_seconds": 1.0,
                    "active_review_seconds": 2.0,
                    "active_human_seconds": 2.0,
                    "write_seconds": 1.0,
                    "external_write_count": 3.0,
                }
            )
        for name, seconds in measurements.items():
            ledger.record_measurement(attempt_id, name, seconds)
        ledger.record_gemini_usage(attempt_id, {"total_token_count": 18})
        ledger.record_policy_result(attempt_id, policy.model_dump(mode="json"))
        if step == "run":
            for result in actionable:
                frame = tmp_path / f"{result.candidate_id}.png"
                frame.write_bytes(_png())
                frame_paths[result.candidate_id] = frame
                ledger.record_evidence_frame(
                    attempt_id,
                    EvidenceFrameRecord(
                        candidate_id=result.candidate_id,
                        timestamp_seconds=result.evidence_frame_seconds,
                        status="extracted",
                        path=str(frame),
                    ),
                )
            for number, result in enumerate(actionable, start=1):
                approval = build_approval(
                    SOURCE,
                    result,
                    "demo/feedback",
                    manual_review_confirmed=result.route == "manual_review",
                )
                ledger.record_approval(attempt_id, approval)
                write_id = ledger.record_write_pending(attempt_id, approval)
                record = IssueRecord(
                    source_sha256=SOURCE,
                    candidate_id=result.candidate_id,
                    destination_repository="demo/feedback",
                    issue_number=number,
                    html_url=f"https://example.test/issues/{number}",
                    marker=marker_for(SOURCE, result.candidate_id),
                    payload_hash=approval.payload_hash,
                    recorded_at=datetime.now(UTC).isoformat(),
                )
                ledger.record_write_state(write_id, attempt_id, approval, "written", issue_record=record)
        acceptance_store.finish(attempt_id, qualified=True, metrics=measurements)

    images: dict[str, Path] = {}
    for label in IMAGE_SEQUENCE:
        path = tmp_path / f"{label}.png"
        path.write_bytes(_png())
        images[label] = path
    gif = tmp_path / "run.gif"
    gif.write_bytes(_gif())
    timeline = GifTimeline(
        final_attempt_id=attempt_ids[-1],
        events=tuple(
            GifEvent(
                label=label,
                attempt_id=attempt_ids[-1],
                elapsed_seconds=float(index + 1),
                description=f"Recorded {label}.",
            )
            for index, label in enumerate(GIF_EVENT_SEQUENCE)
        ),
        removed_waits=(
            RemovedWait(label="Gemini processing", elapsed_seconds=41.25),
            RemovedWait(label="GitHub confirmation", elapsed_seconds=3.5),
        ),
    )
    incident_artifact = tmp_path / "incident.txt"
    incident_artifact.write_text("The public request was rejected before analysis.\n", encoding="utf-8")
    incidents = (
        ProofIncident(
            incident_id="background-rejection",
            kind="background_interaction_rejection",
            attempt_id=rejected_attempt,
            observed_code="background_not_supported",
            observed_status="rejected",
            external_write_count=0,
            summary="The recorded background interaction request was rejected.",
            artifact_path=str(incident_artifact),
        ),
        ProofIncident(
            incident_id="incomplete-analysis",
            kind="incomplete_analysis",
            attempt_id=incomplete_attempt,
            observed_code="processing_unverified",
            observed_status="failed",
            external_write_count=0,
            summary="An injected incomplete analysis produced no external write.",
            artifact_path=str(incident_artifact),
        ),
        ProofIncident(
            incident_id="uncertain-write",
            kind="uncertain_write",
            attempt_id=uncertain_attempt,
            observed_code="write_uncertain",
            observed_status="uncertain",
            external_write_count=0,
            summary="An unavailable write response remained pending reconciliation without an external write.",
            artifact_path=str(incident_artifact),
        ),
    )
    return acceptance_store.record, ledger, images, gif, timeline, incidents


def test_proof_package_is_claim_linked_and_sanitized(tmp_path: Path) -> None:
    acceptance, ledger, images, gif, timeline, incidents = _seed(tmp_path)

    package = build_proof_package(
        acceptance,
        ledger,
        load_ground_truth(GROUND_TRUTH_PATH),
        bundle_dir=tmp_path / "proof-bundle",
        images=images,
        gif=gif,
        gif_timeline=timeline,
        incidents=incidents,
        gif_duration_seconds=25.0,
        duration_probe=lambda _path: 25.0,
    )

    assert package.final_attempt_id == acceptance.qualified_attempt_ids[-1]
    assert package.image_sequence[0].label == IMAGE_SEQUENCE[0]
    assert tuple(item.label for item in package.image_sequence) == IMAGE_SEQUENCE
    assert package.gif.duration_seconds == 25.0
    assert package.deterministic_report.passed is True
    assert len(package.claim_index) >= 5
    assert all(claim.status == "passed" for claim in package.claim_index)
    manifest = json.loads((tmp_path / "proof-bundle" / "manifest.json").read_text())
    assert manifest["source_sha256"] == SOURCE
    sanitized = json.loads((tmp_path / "proof-bundle" / "ledger.json").read_text())
    assert "private-interaction" not in json.dumps(sanitized)
    assert str(tmp_path) not in json.dumps(sanitized)
    assert (tmp_path / "proof-bundle" / "images" / "01-capability-date.png").exists()


def test_proof_package_rejects_unpassed_acceptance(tmp_path: Path) -> None:
    acceptance, ledger, images, gif, timeline, incidents = _seed(tmp_path)
    unpassed = acceptance.model_copy(update={"passed": False})
    with pytest.raises(ProofPackageError, match="acceptance cycle has not passed"):
        build_proof_package(
            unpassed,
            ledger,
            load_ground_truth(GROUND_TRUTH_PATH),
            bundle_dir=tmp_path / "blocked",
            images=images,
            gif=gif,
            gif_timeline=timeline,
            incidents=incidents,
            gif_duration_seconds=25.0,
            duration_probe=lambda _path: 25.0,
        )


def test_package_cli_is_read_only_and_rejects_sensitive_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acceptance, ledger, images, gif, timeline, incidents = _seed(tmp_path)
    original_builder = build_proof_package

    def build_without_probe(*args: Any, **kwargs: Any) -> Any:
        kwargs["duration_probe"] = lambda _path: 25.0
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(cli, "build_proof_package", build_without_probe)
    incident_paths: list[Path] = []
    for index, incident in enumerate(incidents):
        path = tmp_path / f"incident-{index}.json"
        payload = incident.model_dump(mode="json")
        payload["artifact_path"] = str(tmp_path / "incident.txt")
        path.write_text(json.dumps(payload), encoding="utf-8")
        incident_paths.append(path)
    (tmp_path / "timeline.json").write_text(timeline.model_dump_json(), encoding="utf-8")
    image_args = [item for label, path in images.items() for item in ("--image", f"{label}={path}")]
    status = cli.main(
        [
            "package",
            str(ledger.path),
            "--bundle",
            str(tmp_path / "cli-bundle"),
            "--gif",
            str(gif),
            "--gif-timeline",
            str(tmp_path / "timeline.json"),
            *image_args,
            "--incident",
            str(incident_paths[0]),
            "--incident",
            str(incident_paths[1]),
        ]
    )
    assert status == 0
    assert (tmp_path / "cli-bundle" / "manifest.json").exists()
    assert ledger.path.exists()

    sensitive = tmp_path / "sensitive.txt"
    sensitive.write_text("GITHUB_TOKEN=do-not-publish\n", encoding="utf-8")
    unsafe = incidents[0].model_copy(update={"artifact_path": str(sensitive)})
    with pytest.raises(ProofPackageError, match="sensitive"):
        build_proof_package(
            acceptance,
            ledger,
            load_ground_truth(GROUND_TRUTH_PATH),
            bundle_dir=tmp_path / "blocked-sensitive",
            images=images,
            gif=gif,
            gif_timeline=timeline,
            incidents=(unsafe, incidents[1]),
            gif_duration_seconds=25.0,
            duration_probe=lambda _path: 25.0,
        )


def test_binary_media_audit_ignores_random_path_like_bytes_but_rejects_metadata() -> None:
    _audit_public_bytes(b"\x89PNG\r\n\x1ay:\\x00\xff\x00", label="public-image")

    with pytest.raises(ProofPackageError, match="sensitive"):
        _audit_public_bytes(b"\x89PNG\r\n\x89GITHUB_TOKEN=not-a-token", label="public-image")
