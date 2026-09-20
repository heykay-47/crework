"""Build a reviewable, claim-linked proof package from a passed run.

The package builder is deliberately read-only with respect to the source Run
Ledger and GitHub.  It copies only explicitly supplied public assets and an
allowlisted view of persisted proof state into a new frozen bundle.
"""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, ValidationError, model_validator

from feedback_triage.acceptance import AcceptanceRecord
from feedback_triage.evaluation import GroundTruthManifest, ScoreReport, score_policy_result
from feedback_triage.ledger import LedgerInvalid, RunLedger
from feedback_triage.models import Approval, EvidenceFrameRecord, IssueRecord, PolicyResult, RoutedResult, StrictModel
from feedback_triage.persistence import atomic_write_json


IMAGE_SEQUENCE: tuple[str, ...] = (
    "capability-date",
    "fixture-manifest",
    "request-trust",
    "three-run-summary",
    "six-routes",
    "evidence-frames",
    "manual-review-approval",
    "final-issues",
    "sanitized-run-ledger",
)

GIF_EVENT_SEQUENCE: tuple[str, ...] = (
    "third-run-command",
    "verified-analysis",
    "routes-and-review",
    "verified-issues",
)

IncidentKind = Literal[
    "background_interaction_rejection",
    "incomplete_analysis",
    "uncertain_write",
]
ArtifactKind = Literal["image", "gif", "json", "text"]
ProofStatus = Literal["passed", "failed"]


class ProofPackageError(ValueError):
    """The persisted acceptance proof or a public asset cannot be packaged."""


class GifEvent(StrictModel):
    label: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    elapsed_seconds: float = Field(gt=0)
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_elapsed(self) -> "GifEvent":
        if not math.isfinite(self.elapsed_seconds):
            raise ValueError("GIF event elapsed time must be finite")
        return self


class RemovedWait(StrictModel):
    label: str = Field(min_length=1)
    elapsed_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_elapsed(self) -> "RemovedWait":
        if not math.isfinite(self.elapsed_seconds):
            raise ValueError("removed wait elapsed time must be finite")
        return self

    @property
    def display_label(self) -> str:
        return f"{self.label} - removed wait: {self.elapsed_seconds:.2f}s actual"


class GifTimeline(StrictModel):
    """A human-supplied timeline for the edited GIF.

    The timeline names the recorded third-run stages rather than pretending
    that an edited GIF is a live watcher or an execution replay.
    """

    final_attempt_id: str = Field(min_length=1)
    events: tuple[GifEvent, ...]
    removed_waits: tuple[RemovedWait, ...] = Field(min_length=1)
    recorded_run: bool = True
    watcher_used: bool = False
    replay_fabricated_output: bool = False

    @model_validator(mode="after")
    def validate_timeline(self) -> "GifTimeline":
        if tuple(event.label for event in self.events) != GIF_EVENT_SEQUENCE:
            raise ValueError("GIF timeline must cover the third run in the required order")
        if any(event.attempt_id != self.final_attempt_id for event in self.events):
            raise ValueError("GIF events must be tied to the final acceptance attempt")
        if not self.recorded_run:
            raise ValueError("GIF timeline must identify a recorded run")
        if self.watcher_used:
            raise ValueError("proof GIF must not imply a watcher")
        if self.replay_fabricated_output:
            raise ValueError("proof GIF must not claim replay-fabricated output")
        return self


class ProofIncident(StrictModel):
    """Sanitized metadata for a public failure or no-write safety case."""

    incident_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: IncidentKind
    attempt_id: str = Field(min_length=1)
    observed_code: str = Field(min_length=1)
    observed_status: str = Field(min_length=1)
    external_write_count: int = Field(ge=0)
    summary: str = Field(min_length=1)
    # This is an input-only path. It is never serialized into the public
    # manifest; the copied evidence artifact is the public reference.
    artifact_path: str = Field(min_length=1, exclude=True)

    @model_validator(mode="after")
    def validate_safety(self) -> "ProofIncident":
        if self.kind == "background_interaction_rejection" and self.observed_status.casefold() not in {
            "rejected",
            "failed",
            "unsupported",
        }:
            raise ValueError("background interaction incident must show a rejection or failure")
        if self.external_write_count != 0:
            raise ValueError("public proof incidents must demonstrate zero external writes")
        return self


class ProofArtifact(StrictModel):
    artifact_id: str = Field(pattern=r"^artifact_[0-9]{3}$")
    path: str = Field(min_length=1)
    kind: ArtifactKind
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relative_path(self) -> "ProofArtifact":
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("proof artifacts must use bundle-relative paths")
        return self


class ProofClaim(StrictModel):
    claim_id: str = Field(pattern=r"^claim_[a-z0-9_-]+$")
    claim: str = Field(min_length=1)
    executable_check: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    status: ProofStatus
    artifact_ids: tuple[str, ...] = Field(min_length=1)
    attempt_ids: tuple[str, ...] = Field(min_length=1)


class ImageSlot(StrictModel):
    order: int = Field(gt=0)
    label: str = Field(min_length=1)
    artifact_id: str = Field(pattern=r"^artifact_[0-9]{3}$")
    attempt_id: str = Field(min_length=1)


class GifProof(StrictModel):
    artifact_id: str = Field(pattern=r"^artifact_[0-9]{3}$")
    timeline_artifact_id: str = Field(pattern=r"^artifact_[0-9]{3}$")
    duration_seconds: float = Field(ge=20, le=30)
    final_attempt_id: str = Field(min_length=1)
    removed_waits: tuple[RemovedWait, ...] = Field(min_length=1)
    watcher_used: bool
    replay_fabricated_output: bool


class FingerprintEpoch(StrictModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    current: bool
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fingerprint_inputs: dict[str, str]


class ProofPackage(StrictModel):
    version: Literal[1] = 1
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    settled_fingerprint_hashes: tuple[str, ...] = Field(min_length=1)
    fingerprint_epochs: tuple[FingerprintEpoch, ...] = Field(min_length=1)
    acceptance_attempt_ids: tuple[str, ...] = Field(min_length=3)
    final_attempt_id: str = Field(min_length=1)
    deterministic_report: ScoreReport
    sanitized_ledger_path: str
    sanitized_ledger_artifact_id: str = Field(pattern=r"^artifact_[0-9]{3}$")
    claim_index_path: str
    claim_index: tuple[ProofClaim, ...] = Field(min_length=1)
    image_sequence: tuple[ImageSlot, ...]
    gif: GifProof
    incidents: tuple[ProofIncident, ...] = Field(min_length=2)
    artifacts: tuple[ProofArtifact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_package(self) -> "ProofPackage":
        if tuple(item.label for item in self.image_sequence) != IMAGE_SEQUENCE:
            raise ValueError("proof package must contain the fixed nine-image sequence")
        if self.gif.final_attempt_id != self.final_attempt_id:
            raise ValueError("proof GIF must point to the final acceptance attempt")
        if not any(item.kind == "background_interaction_rejection" for item in self.incidents):
            raise ValueError("proof package must include the background interaction rejection")
        if not any(item.kind in {"incomplete_analysis", "uncertain_write"} for item in self.incidents):
            raise ValueError("proof package must include a zero-write incomplete or uncertain case")
        artifact_ids = {artifact.artifact_id for artifact in self.artifacts}
        if any(
            artifact_id not in artifact_ids
            for claim in self.claim_index
            for artifact_id in claim.artifact_ids
        ):
            raise ValueError("claim index references an unknown proof artifact")
        return self


_SENSITIVE_BYTES = re.compile(
    rb"(?:(?:[A-Z][A-Z0-9]*(?:_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS?)|"
    rb"HOME|PATH|PWD|USER|SHELL|VIRTUAL_ENV)\s*=|"
    rb"GEMINI_API_KEY|GITHUB_TOKEN|GOOGLE_API_KEY|Authorization\s*:\s*(?:Bearer|Basic)|"
    rb"(?:gh[pousr]_|github_pat_|AIza|sk-)[A-Za-z0-9_\-.]{12,}|"
    rb"(?:/(?:home|Users|private|tmp|var|mnt|root|opt|workspace|workspaces)/|"
    rb"[A-Za-z]:\\[A-Za-z0-9_. -]{4,})|"
    rb"(?:\.\.?/){2}|"
    rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e]{4,}")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_FINGERPRINT_INPUTS = frozenset(
    {
        "source",
        "model",
        "prompt",
        "schema",
        "context",
        "policy",
        "dependency",
        "implementation",
        "request_trust",
        "fixture_version",
        "ground_truth",
    }
)
_TEXT_SUFFIXES = frozenset({".json", ".txt", ".md", ".csv", ".log", ".html", ".xml"})
_SENSITIVE_JSON_KEYS = frozenset(
    {
        "api_response",
        "authorization",
        "body",
        "credential",
        "environment",
        "env",
        "headers",
        "private_key",
        "raw_response",
        "request_headers",
        "response",
        "secret",
        "token",
    }
)
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ProofPackageError(f"could not read proof asset: {path.name}") from error
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_file(path: Path, *, label: str) -> Path:
    if not path.is_file():
        raise ProofPackageError(f"{label} is not a regular file")
    try:
        if path.stat().st_size <= 0:
            raise ProofPackageError(f"{label} is empty")
    except OSError as error:
        raise ProofPackageError(f"could not inspect {label}") from error
    return path


def _audit_public_bytes(data: bytes, *, label: str) -> None:
    # Binary media can contain arbitrary byte pairs that resemble a Windows
    # path or credential prefix by chance.  Inspect the whole payload for
    # text artifacts, but apply the sensitive-pattern audit to printable runs
    # for images/GIFs so real metadata is still rejected without rejecting
    # unrelated compressed pixels.
    printable = b"\n".join(_PRINTABLE_RUN.findall(data))
    if _SENSITIVE_BYTES.search(printable):
        raise ProofPackageError(f"sensitive credential, personal data, or private path found in {label}")


def _skip_gif_subblocks(data: bytes, offset: int) -> int:
    while True:
        if offset >= len(data):
            raise ProofPackageError("proof GIF has an unterminated data block")
        size = data[offset]
        offset += 1
        if size == 0:
            return offset
        offset += size
        if offset > len(data):
            raise ProofPackageError("proof GIF has truncated data")


def _validate_gif_bytes(data: bytes) -> None:
    """Validate the GIF block structure instead of accepting a header-only stub."""

    if len(data) < 14 or not data.startswith((b"GIF87a", b"GIF89a")):
        raise ProofPackageError("proof GIF is not a GIF file")
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    if width <= 0 or height <= 0:
        raise ProofPackageError("proof GIF has invalid dimensions")
    offset = 13
    packed = data[10]
    if packed & 0x80:
        offset += 3 * (2 ** ((packed & 0x07) + 1))
    if offset > len(data):
        raise ProofPackageError("proof GIF has a truncated color table")
    frames = 0
    while offset < len(data):
        block = data[offset]
        offset += 1
        if block == 0x3B:
            if frames == 0:
                raise ProofPackageError("proof GIF has no image frames")
            return
        if block == 0x21:
            if offset >= len(data):
                raise ProofPackageError("proof GIF has a truncated extension")
            label = data[offset]
            offset += 1
            if label == 0xF9:
                if offset + 5 > len(data) or data[offset] != 4:
                    raise ProofPackageError("proof GIF has an invalid graphics control extension")
                offset += 5
                if offset >= len(data) or data[offset] != 0:
                    raise ProofPackageError("proof GIF has an unterminated graphics control extension")
                offset += 1
            else:
                offset = _skip_gif_subblocks(data, offset)
            continue
        if block == 0x2C:
            if offset + 9 > len(data):
                raise ProofPackageError("proof GIF has a truncated image descriptor")
            local_packed = data[offset + 8]
            offset += 9
            if local_packed & 0x80:
                offset += 3 * (2 ** ((local_packed & 0x07) + 1))
            if offset >= len(data):
                raise ProofPackageError("proof GIF has no image data")
            offset += 1
            offset = _skip_gif_subblocks(data, offset)
            frames += 1
            continue
        raise ProofPackageError("proof GIF contains an invalid block")
    raise ProofPackageError("proof GIF has no trailer")


def _sanitize_public_text(text: str, *, label: str) -> str:
    encoded = text.encode("utf-8")
    _audit_public_bytes(encoded, label=label)
    return text


def _sanitize_public_json(text: str, *, label: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProofPackageError(f"{label} is not valid JSON") from error

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str) and key.casefold() in _SENSITIVE_JSON_KEYS:
                    raise ProofPackageError(f"unsanitized API or credential field found in {label}")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    sanitized = json.dumps(value, indent=2, sort_keys=True)
    _audit_public_bytes(sanitized.encode("utf-8"), label=label)
    return sanitized


def _media_type(path: Path, *, expected_kind: ArtifactKind | None = None) -> tuple[ArtifactKind, str]:
    suffix = path.suffix.casefold()
    if suffix == ".gif":
        return "gif", "image/gif"
    if suffix in _IMAGE_MEDIA_TYPES:
        return "image", _IMAGE_MEDIA_TYPES[suffix]
    guessed = mimetypes.guess_type(path.name)[0]
    if suffix in _TEXT_SUFFIXES:
        return ("json" if suffix == ".json" else "text"), guessed or "text/plain"
    if expected_kind is not None:
        return expected_kind, guessed or "application/octet-stream"
    raise ProofPackageError(f"unsupported proof artifact media type: {path.suffix or 'none'}")


def probe_media_duration(path: Path) -> float:
    """Read a media duration without a shell; the CLI always uses this seam."""

    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        value = json.loads(completed.stdout).get("format", {}).get("duration")
        duration = float(value)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ProofPackageError("could not verify GIF duration with ffprobe") from error
    if duration < 20 or duration > 30:
        raise ProofPackageError("proof GIF duration must be between 20 and 30 seconds")
    return duration


def _copy_public_asset(
    source: Path,
    destination: Path,
    *,
    source_sha256: str,
    attempt_ids: Sequence[str],
    expected_kind: ArtifactKind | None = None,
    artifact_id: str,
) -> ProofArtifact:
    _require_file(source, label="proof asset")
    kind, media_type = _media_type(source, expected_kind=expected_kind)
    try:
        data = source.read_bytes()
    except OSError as error:
        raise ProofPackageError("could not read proof asset") from error
    _audit_public_bytes(data, label="proof asset")
    if kind == "gif":
        _validate_gif_bytes(data)
    if kind == "image" and not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")):
        raise ProofPackageError("proof screenshot is not a supported image file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, destination)
    except OSError as error:
        raise ProofPackageError("could not copy proof asset into the bundle") from error
    return ProofArtifact(
        artifact_id=artifact_id,
        path=destination.relative_to(destination.parents[2]).as_posix()
        if len(destination.parents) >= 3
        else destination.name,
        kind=kind,
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
        source_sha256=source_sha256,
        attempt_ids=tuple(attempt_ids),
    )


def _copy_asset_with_relative_path(
    source: Path,
    stage: Path,
    relative_path: str,
    *,
    source_sha256: str,
    attempt_ids: Sequence[str],
    expected_kind: ArtifactKind | None,
    artifact_id: str,
) -> ProofArtifact:
    destination = stage / relative_path
    artifact = _copy_public_asset(
        source,
        destination,
        source_sha256=source_sha256,
        attempt_ids=attempt_ids,
        expected_kind=expected_kind,
        artifact_id=artifact_id,
    )
    return artifact.model_copy(update={"path": PurePosixPath(relative_path).as_posix()})


def _write_artifact(
    stage: Path,
    relative_path: str,
    data: bytes,
    *,
    kind: ArtifactKind,
    media_type: str,
    source_sha256: str,
    attempt_ids: Sequence[str],
    artifact_id: str,
) -> ProofArtifact:
    _audit_public_bytes(data, label=relative_path)
    destination = stage / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return ProofArtifact(
        artifact_id=artifact_id,
        path=relative_path,
        kind=kind,
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
        source_sha256=source_sha256,
        attempt_ids=tuple(attempt_ids),
    )


def _load_policy(attempt: Mapping[str, Any]) -> PolicyResult | None:
    value = attempt.get("policy_result")
    if not isinstance(value, dict):
        return None
    try:
        return PolicyResult.model_validate(value)
    except ValidationError:
        return None


def _has_verified_analysis_proof(attempt: Mapping[str, Any]) -> bool:
    """Require the same persisted trust evidence used to qualify an analysis."""

    measurements = attempt.get("measurements")
    usage = attempt.get("gemini_usage")
    processing_pair_count = attempt.get("processing_pair_count")
    interaction_id = attempt.get("interaction_id")
    if (
        attempt.get("status") != "verified"
        or not isinstance(attempt.get("analysis"), dict)
        or not isinstance(measurements, dict)
        or not isinstance(usage, dict)
        or not usage
        or not isinstance(interaction_id, str)
        or not interaction_id.strip()
        or isinstance(processing_pair_count, bool)
        or not isinstance(processing_pair_count, int)
        or processing_pair_count <= 0
    ):
        return False
    if any(
        not isinstance(name, str)
        or not name.strip()
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for name, value in usage.items()
    ):
        return False
    required = {"upload_seconds", "analysis_seconds", "wall_clock_seconds", "semantic_score_passed"}
    if not required <= set(measurements):
        return False
    return all(
        not isinstance(measurements[name], bool)
        and isinstance(measurements[name], (int, float))
        and math.isfinite(measurements[name])
        and measurements[name] >= 0
        for name in required
    ) and measurements["semantic_score_passed"] == 1.0


def _attempt_by_id(ledger: RunLedger) -> dict[str, dict[str, Any]]:
    return {
        cast(str, attempt["attempt_id"]): attempt
        for attempt in ledger.attempts
        if isinstance(attempt.get("attempt_id"), str)
    }


def _require_acceptance(
    acceptance: AcceptanceRecord,
    ledger: RunLedger,
    ground_truth: GroundTruthManifest,
) -> tuple[str, dict[str, Any], PolicyResult, ScoreReport]:
    if not acceptance.passed:
        raise ProofPackageError("acceptance cycle has not passed")
    if acceptance.source_sha256 != ledger.source_sha256 or acceptance.source_sha256 != ground_truth.source_sha256:
        raise ProofPackageError("proof source identity does not match the frozen fixture")
    if acceptance.fingerprint != ledger.analysis_fingerprint:
        raise ProofPackageError("acceptance fingerprint does not match the current Run Ledger")
    if len(acceptance.qualified_attempt_ids) != 3 or len(set(acceptance.qualified_attempt_ids)) != 3:
        raise ProofPackageError("proof package requires exactly three distinct qualifying attempts")
    attempts = _attempt_by_id(ledger)
    if any(attempt_id not in attempts for attempt_id in acceptance.qualified_attempt_ids):
        raise ProofPackageError("acceptance references an attempt missing from the current Run Ledger")
    qualified_attempt_ids = tuple(acceptance.qualified_attempt_ids)
    positions = [
        next(index for index, item in enumerate(ledger.attempts) if item.get("attempt_id") == attempt_id)
        for attempt_id in qualified_attempt_ids
    ]
    if positions != sorted(positions):
        raise ProofPackageError("qualified acceptance attempts are not in persisted run order")
    qualified_measurements = {
        item.attempt_id for item in acceptance.measurements if item.qualified
    }
    if not set(qualified_attempt_ids) <= qualified_measurements:
        raise ProofPackageError("acceptance proof is missing a qualifying measurement")
    for attempt_id in qualified_attempt_ids:
        attempt = attempts[attempt_id]
        if not _has_verified_analysis_proof(attempt):
            raise ProofPackageError("a qualifying acceptance attempt lacks verified analysis proof")
        attempt_policy = _load_policy(attempt)
        if attempt_policy is None:
            raise ProofPackageError("a qualifying acceptance attempt has no valid persisted policy result")
        attempt_report = score_policy_result(
            attempt_policy,
            ground_truth,
            source_sha256=ledger.source_sha256,
            duration_seconds=ground_truth.duration_seconds,
        )
        if not attempt_report.passed:
            raise ProofPackageError("a qualifying acceptance attempt does not reproduce the deterministic pass")
    final_attempt_id = qualified_attempt_ids[-1]
    final_attempt = attempts[final_attempt_id]
    if not ledger.attempts or ledger.attempts[-1].get("attempt_id") != final_attempt_id:
        raise ProofPackageError("final acceptance attempt is not the latest persisted attempt")
    if final_attempt.get("status") != "verified" or not isinstance(final_attempt.get("processing_pair_count"), int):
        raise ProofPackageError("final acceptance attempt is not a verified analysis")
    policy = _load_policy(final_attempt)
    if policy is None:
        raise ProofPackageError("final acceptance attempt has no valid persisted policy result")
    report = score_policy_result(
        policy,
        ground_truth,
        source_sha256=ledger.source_sha256,
        duration_seconds=ground_truth.duration_seconds,
    )
    if not report.passed:
        raise ProofPackageError("recomputed deterministic report does not pass")
    return final_attempt_id, final_attempt, policy, report


def _load_fingerprint_epochs(ledger: RunLedger) -> tuple[tuple[RunLedger, bool], ...]:
    epochs: list[tuple[RunLedger, bool]] = [(ledger, True)]
    for archive in sorted(ledger.path.parent.glob("ledger-*.json")):
        try:
            archived = RunLedger.load(archive)
        except (LedgerInvalid, OSError, ValueError) as error:
            raise ProofPackageError("a settled fingerprint ledger archive is invalid") from error
        epochs.append((archived, False))
    fingerprints = [epoch.analysis_fingerprint for epoch, _ in epochs]
    if len(fingerprints) != len(set(fingerprints)):
        raise ProofPackageError("settled fingerprint ledgers are duplicated")
    return tuple(epochs)


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "redacted-url"
    if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password:
        return "redacted-url"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _safe_result(result: RoutedResult) -> dict[str, Any]:
    return {
        "candidate_id": result.candidate_id,
        "topic_key": result.topic_key,
        "type": result.type,
        "intent": result.intent,
        "route": result.route,
        "confidence": result.confidence,
        "evidence_frame_seconds": result.evidence_frame_seconds,
        "approval_eligible": result.approval_eligible,
        "visually_inferred": result.visually_inferred,
        "reason_code": result.reason_code,
        "evidence_span_count": len(result.evidence),
    }


def _safe_attempt(
    attempt: Mapping[str, Any],
    *,
    fingerprint: str,
    frame_paths: Mapping[str, str],
) -> dict[str, Any]:
    policy = _load_policy(attempt)
    analysis = attempt.get("analysis")
    analysis_dict = analysis if isinstance(analysis, dict) else {}
    safe: dict[str, Any] = {
        "attempt_id": attempt.get("attempt_id"),
        "status": attempt.get("status"),
        "epoch_fingerprint": fingerprint,
        "started_at": attempt.get("started_at"),
        "completed_at": attempt.get("completed_at"),
        "interaction_verified": bool(attempt.get("interaction_id")) and attempt.get("status") == "verified",
        "processing_pair_count": attempt.get("processing_pair_count", 0),
        "measurements": dict(attempt.get("measurements", {})) if isinstance(attempt.get("measurements"), dict) else {},
        "gemini_usage": dict(attempt.get("gemini_usage", {})) if isinstance(attempt.get("gemini_usage"), dict) else {},
        "analysis": {
            "schema_version": analysis_dict.get("schema_version"),
            "observation_count": len(analysis_dict.get("observations", []))
            if isinstance(analysis_dict.get("observations"), list)
            else 0,
        },
        "policy_result": {
            "schema_version": "1.0",
            "result_count": len(policy.results),
            "results": [_safe_result(result) for result in policy.results],
        }
        if policy is not None
        else None,
    }
    failure = attempt.get("failure")
    if isinstance(failure, dict) and isinstance(failure.get("code"), str):
        safe["failure_code"] = _sanitize_public_text(failure["code"], label="failure code")
    frames: list[dict[str, Any]] = []
    values = attempt.get("evidence_frames", [])
    if isinstance(values, list):
        for value in values:
            try:
                frame = EvidenceFrameRecord.model_validate(value)
            except ValidationError:
                continue
            frames.append(
                {
                    "candidate_id": frame.candidate_id,
                    "timestamp_seconds": frame.timestamp_seconds,
                    "status": frame.status,
                    "path": frame_paths.get(frame.candidate_id) if frame.status == "extracted" else None,
                }
            )
    safe["evidence_frames"] = frames
    return safe


def _safe_acceptance(acceptance: AcceptanceRecord) -> dict[str, Any]:
    baseline = acceptance.manual_baseline
    return {
        "source_sha256": acceptance.source_sha256,
        "fingerprint": acceptance.fingerprint,
        "qualified_attempt_ids": list(acceptance.qualified_attempt_ids),
        "passed": acceptance.passed,
        "measurements": [
            {
                "attempt_id": item.attempt_id,
                "qualified": item.qualified,
                "metrics": dict(item.metrics),
            }
            for item in acceptance.measurements
        ],
        "reset_count": len(acceptance.reset_events),
        "manual_baseline": {
            "source_sha256": baseline.source_sha256,
            "equivalent_issue_count": baseline.equivalent_issue_count,
            "watch_seconds": baseline.watch_seconds,
            "issue_writing_seconds": baseline.issue_writing_seconds,
            "active_human_seconds": baseline.active_human_seconds,
            "measured_at": baseline.measured_at,
        }
        if baseline is not None
        else None,
    }


def _safe_fingerprint_inputs(ledger: RunLedger) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in ledger.fingerprint_inputs.items():
        safe_key = _sanitize_public_text(key, label="fingerprint input name")
        if key not in _PUBLIC_FINGERPRINT_INPUTS and not _HASH_PATTERN.fullmatch(value):
            safe_value = f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
        else:
            safe_value = _sanitize_public_text(value, label=f"fingerprint input {key}")
        safe[safe_key] = safe_value
    return safe


def _safe_epoch(
    ledger: RunLedger,
    *,
    current: bool,
    frame_paths: Mapping[str, str],
) -> dict[str, Any]:
    attempts = [
        _safe_attempt(attempt, fingerprint=ledger.analysis_fingerprint, frame_paths=frame_paths)
        for attempt in ledger.attempts
    ]
    approvals = [
        {
            "attempt_id": approval_value.get("attempt_id"),
            "candidate_id": approval_value.get("candidate_id"),
            "destination_repository": approval_value.get("destination_repository"),
            "candidate_snapshot_hash": approval_value.get("candidate_snapshot_hash"),
            "candidate_payload_hash": approval_value.get("candidate_payload_hash"),
            "payload_hash": approval_value.get("payload_hash"),
            "approved_at": approval_value.get("approved_at"),
            "manual_review_confirmed": approval_value.get("manual_review_confirmed", False),
            "operator_label_present": bool(approval_value.get("operator_label")),
        }
        for approval_value in ledger.approval_events
    ]
    writes: list[dict[str, Any]] = []
    for write_value in ledger.write_attempts:
        writes.append(
            {
                "write_id": write_value.get("write_id"),
                "attempt_id": write_value.get("attempt_id"),
                "candidate_id": write_value.get("candidate_id"),
                "destination_repository": write_value.get("destination_repository"),
                "state": write_value.get("state"),
                "candidate_snapshot_hash": write_value.get("candidate_snapshot_hash"),
                "candidate_payload_hash": write_value.get("candidate_payload_hash"),
                "payload_hash": write_value.get("payload_hash"),
                "issue_number": write_value.get("issue_number"),
                "issue_url": _safe_url(write_value.get("issue_url", ""))
                if isinstance(write_value.get("issue_url"), str)
                else None,
            }
        )
    issue_records = [
        {
            "candidate_id": record.candidate_id,
            "destination_repository": record.destination_repository,
            "issue_number": record.issue_number,
            "html_url": _safe_url(record.html_url),
            "marker": record.marker,
            "payload_hash": record.payload_hash,
            "recorded_at": record.recorded_at,
        }
        for record in ledger.issue_records
    ]
    safe_errors = [
        {"code": value.get("code"), "stage": value.get("stage"), "attempt_id": value.get("attempt_id")}
        for value in ledger.reconciliation_errors
        if isinstance(value, dict)
    ]
    safe_conflicts = [
        {
            "candidate_id": value.get("candidate_id"),
            "destination_repository": value.get("destination_repository"),
            "selected_issue_number": value.get("selected_issue_number"),
            "selected_issue_url": _safe_url(value.get("selected_issue_url", ""))
            if isinstance(value.get("selected_issue_url"), str)
            else None,
        }
        for value in ledger.conflict_resolutions
        if isinstance(value, dict)
    ]
    return {
        "fingerprint": ledger.analysis_fingerprint,
        "current": current,
        "source_sha256": ledger.source_sha256,
        "fingerprint_inputs": _safe_fingerprint_inputs(ledger),
        "attempts": attempts,
        "approvals": approvals,
        "declines": [
            {
                "attempt_id": value.get("attempt_id"),
                "candidate_id": value.get("candidate_id"),
                "destination_repository": value.get("destination_repository"),
                "state": value.get("state"),
            }
            for value in ledger.declines
            if isinstance(value, dict)
        ],
        "write_attempts": writes,
        "issue_records": issue_records,
        "reconciliation_errors": safe_errors,
        "conflict_resolutions": safe_conflicts,
    }


def _safe_image_mapping(images: Mapping[str, Path]) -> dict[str, Path]:
    if set(images) != set(IMAGE_SEQUENCE):
        missing = sorted(set(IMAGE_SEQUENCE) - set(images))
        extra = sorted(set(images) - set(IMAGE_SEQUENCE))
        raise ProofPackageError(f"proof package requires exactly nine named images (missing={missing}, extra={extra})")
    return {label: Path(images[label]) for label in IMAGE_SEQUENCE}


def _validate_final_proof(
    final_attempt: Mapping[str, Any],
    policy: PolicyResult,
    ground_truth: GroundTruthManifest,
) -> tuple[dict[str, Path], set[str]]:
    frames: dict[str, Path] = {}
    for value in final_attempt.get("evidence_frames", []):
        try:
            record = EvidenceFrameRecord.model_validate(value)
        except ValidationError as error:
            raise ProofPackageError("final acceptance attempt contains an invalid Evidence Frame") from error
        if record.status == "extracted" and record.path is not None:
            path = Path(record.path)
            if not path.is_absolute():
                path = Path(record.path)
            if not path.is_file():
                raise ProofPackageError("final Evidence Frame asset is missing")
            frames[record.candidate_id] = path
    required_frame_candidates = {
        case.required.candidate_id
        for case in ground_truth.cases
        if case.evidence_frame_required and case.required.candidate_id is not None
    }
    if not required_frame_candidates <= set(frames):
        raise ProofPackageError("final acceptance proof is missing one or more required Evidence Frames")
    actionable = {result.candidate_id for result in policy.results if result.route in {"candidate", "manual_review"}}
    if not actionable <= set(frames):
        raise ProofPackageError("final acceptance proof is missing an actionable Evidence Frame")
    return frames, actionable


def _validate_final_writes(ledger: RunLedger, final_attempt_id: str, actionable: set[str]) -> int:
    approved_candidates = {
        value.get("candidate_id")
        for value in ledger.approval_events
        if value.get("attempt_id") == final_attempt_id and value.get("state") == "approved"
    }
    if approved_candidates != actionable:
        raise ProofPackageError("final acceptance proof is missing Approval for one or more actionable routes")

    written = [
        value
        for value in ledger.latest_write_attempts()
        if value.get("attempt_id") == final_attempt_id and value.get("state") == "written"
    ]
    written_candidates = {value.get("candidate_id") for value in written}
    if len(written) != len(actionable) or written_candidates != actionable:
        raise ProofPackageError("final acceptance proof is missing a verified written Issue outcome")

    persisted_records = ledger.issue_records
    for value in written:
        raw_record = value.get("issue_record")
        if not isinstance(raw_record, dict):
            raise ProofPackageError("final written Issue outcome has no Issue Record")
        try:
            record = IssueRecord.model_validate(raw_record)
        except ValidationError as error:
            raise ProofPackageError("final written Issue outcome has an invalid Issue Record") from error
        if record.source_sha256 != ledger.source_sha256 or record.candidate_id != value.get("candidate_id"):
            raise ProofPackageError("final Issue Record does not match its written outcome")
        if not any(existing == record for existing in persisted_records):
            raise ProofPackageError("final Issue Record is not persisted in the Run Ledger")
        approval_event = next(
            event
            for event in ledger.approval_events
            if event.get("attempt_id") == final_attempt_id and event.get("candidate_id") == record.candidate_id
        )
        try:
            approval = Approval.model_validate(
                {key: item for key, item in approval_event.items() if key not in {"attempt_id", "state"}}
            )
        except ValidationError as error:
            raise ProofPackageError("final Approval metadata is invalid") from error
        if (
            value.get("source_sha256") != ledger.source_sha256
            or value.get("marker") != record.marker
            or approval.destination_repository != record.destination_repository
            or approval.payload_hash != record.payload_hash
        ):
            raise ProofPackageError("final written Issue outcome does not match its Approval")
    return len(written)


def _validate_incident_provenance(
    epochs: Sequence[tuple[RunLedger, bool]], incidents: Sequence[ProofIncident]
) -> None:
    attempts = {
        attempt_id: attempt
        for epoch, _ in epochs
        for attempt_id, attempt in _attempt_by_id(epoch).items()
    }
    write_attempts = tuple(
        value
        for epoch, _ in epochs
        for value in epoch.latest_write_attempts()
    )
    for incident in incidents:
        attempt = attempts[incident.attempt_id]
        related_writes = tuple(value for value in write_attempts if value.get("attempt_id") == incident.attempt_id)
        measurements = attempt.get("measurements")
        persisted_external_count = measurements.get("external_write_count") if isinstance(measurements, dict) else None
        if (
            isinstance(persisted_external_count, (int, float))
            and not isinstance(persisted_external_count, bool)
            and persisted_external_count != 0
        ):
            raise ProofPackageError(f"incident {incident.incident_id} has persisted external-write measurements")
        if incident.kind in {"background_interaction_rejection", "incomplete_analysis"}:
            if incident.kind == "background_interaction_rejection" and "background" not in incident.observed_code.casefold():
                raise ProofPackageError("background rejection incident does not identify a background failure")
            failure = attempt.get("failure")
            failure_detail = failure.get("detail", "") if isinstance(failure, dict) else ""
            if (
                attempt.get("status") != "failed"
                or not isinstance(failure, dict)
                or failure.get("code") != incident.observed_code
            ):
                raise ProofPackageError(
                    f"incident {incident.incident_id} is not backed by a persisted failed analysis"
                )
            if incident.kind == "incomplete_analysis" and incident.observed_status != "failed":
                raise ProofPackageError(f"incident {incident.incident_id} has an invalid failed status")
            if incident.kind == "background_interaction_rejection" and (
                incident.observed_status != "rejected"
                or "background" not in failure_detail.casefold()
                or "reject" not in failure_detail.casefold()
            ):
                raise ProofPackageError(
                    f"incident {incident.incident_id} is not backed by a background rejection"
                )
            if any(
                value.get("state") in {"write_pending", "written", "write_uncertain", "external_write_conflict"}
                for value in related_writes
            ):
                raise ProofPackageError(
                    f"incident {incident.incident_id} has a persisted unsafe write"
                )
        else:
            if not any(
                value.get("state") == "write_uncertain" and incident.observed_code == "write_uncertain"
                for value in related_writes
            ):
                raise ProofPackageError(
                    f"incident {incident.incident_id} is not backed by a persisted uncertain write"
                )
            if any(
                value.get("state") in {"write_pending", "written", "external_write_conflict"}
                for value in related_writes
            ):
                raise ProofPackageError(
                    f"incident {incident.incident_id} has a persisted external write"
                )


def _next_artifact_id(artifacts: list[ProofArtifact]) -> str:
    return f"artifact_{len(artifacts) + 1:03d}"


def _incident_json(incident: ProofIncident, artifact_id: str) -> bytes:
    summary = _sanitize_public_text(incident.summary, label=incident.incident_id)
    payload = {
        "incident_id": incident.incident_id,
        "kind": incident.kind,
        "attempt_id": incident.attempt_id,
        "observed_code": _sanitize_public_text(incident.observed_code, label=incident.incident_id),
        "observed_status": _sanitize_public_text(incident.observed_status, label=incident.incident_id),
        "external_write_count": incident.external_write_count,
        "summary": summary,
        "evidence_artifact_id": artifact_id,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def build_proof_package(
    acceptance: AcceptanceRecord,
    ledger: RunLedger,
    ground_truth: GroundTruthManifest,
    *,
    bundle_dir: Path,
    images: Mapping[str, Path],
    gif: Path,
    gif_timeline: GifTimeline,
    incidents: Sequence[ProofIncident],
    gif_duration_seconds: float | None = None,
    duration_probe: Callable[[Path], float] = probe_media_duration,
) -> ProofPackage:
    """Freeze a passed run into a sanitized proof bundle.

    This is the public seam used by the CLI and tests.  It performs no model
    calls, re-analysis, or remote writes.  Existing bundle paths are rejected
    so a successful package remains immutable and traceable.
    """

    bundle_dir = Path(bundle_dir)
    if bundle_dir.exists():
        raise ProofPackageError("proof bundle destination already exists")
    image_paths = _safe_image_mapping(images)
    if len(incidents) < 2:
        raise ProofPackageError("proof package requires background rejection and zero-write failure incidents")
    try:
        final_attempt_id, final_attempt, policy, report = _require_acceptance(acceptance, ledger, ground_truth)
        frame_sources, actionable = _validate_final_proof(final_attempt, policy, ground_truth)
        written_issue_count = _validate_final_writes(ledger, final_attempt_id, actionable)
    except (LedgerInvalid, ValidationError) as error:
        raise ProofPackageError("persisted proof state is invalid") from error
    if gif_timeline.final_attempt_id != final_attempt_id:
        raise ProofPackageError("GIF timeline is not tied to the final acceptance attempt")
    epochs = _load_fingerprint_epochs(ledger)
    all_attempt_ids = {attempt_id for epoch, _ in epochs for attempt_id in _attempt_by_id(epoch)}
    if any(incident.attempt_id not in all_attempt_ids for incident in incidents):
        raise ProofPackageError("each public incident must name a persisted attempt")
    if not any(incident.kind == "background_interaction_rejection" for incident in incidents):
        raise ProofPackageError("proof package requires the background interaction rejection")
    if not any(incident.kind in {"incomplete_analysis", "uncertain_write"} for incident in incidents):
        raise ProofPackageError("proof package requires an incomplete or uncertain zero-write case")
    for incident in incidents:
        if incident.kind in {"incomplete_analysis", "uncertain_write"} and incident.external_write_count != 0:
            raise ProofPackageError("unsafe write evidence is not zero")
    _validate_incident_provenance(epochs, incidents)

    duration = duration_probe(Path(gif))
    if gif_duration_seconds is not None and not math.isclose(duration, gif_duration_seconds, rel_tol=0, abs_tol=0.01):
        raise ProofPackageError("GIF duration probe disagrees with supplied duration")
    if duration < 20 or duration > 30:
        raise ProofPackageError("proof GIF duration must be between 20 and 30 seconds")
    if tuple(event.label for event in gif_timeline.events) != GIF_EVENT_SEQUENCE:
        raise ProofPackageError("GIF timeline must cover the third run in the required order")

    parent = bundle_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{bundle_dir.name}-", dir=parent))
    artifacts: list[ProofArtifact] = []
    try:
        image_slots: list[ImageSlot] = []
        for order, label in enumerate(IMAGE_SEQUENCE, start=1):
            artifact_id = _next_artifact_id(artifacts)
            artifact = _copy_asset_with_relative_path(
                image_paths[label],
                stage,
                f"images/{order:02d}-{label}{image_paths[label].suffix.casefold()}",
                source_sha256=ledger.source_sha256,
                attempt_ids=(final_attempt_id,),
                expected_kind="image",
                artifact_id=artifact_id,
            )
            artifacts.append(artifact)
            image_slots.append(ImageSlot(order=order, label=label, artifact_id=artifact_id, attempt_id=final_attempt_id))

        gif_artifact_id = _next_artifact_id(artifacts)
        gif_artifact = _copy_asset_with_relative_path(
            Path(gif),
            stage,
            "media/third-run.gif",
            source_sha256=ledger.source_sha256,
            attempt_ids=(final_attempt_id,),
            expected_kind="gif",
            artifact_id=gif_artifact_id,
        )
        artifacts.append(gif_artifact)

        frame_paths: dict[str, str] = {}
        for candidate_id, frame_source in sorted(frame_sources.items()):
            frame_artifact_id = _next_artifact_id(artifacts)
            frame_artifact = _copy_asset_with_relative_path(
                frame_source,
                stage,
                f"evidence-frames/{candidate_id}.png",
                source_sha256=ledger.source_sha256,
                attempt_ids=(final_attempt_id,),
                expected_kind="image",
                artifact_id=frame_artifact_id,
            )
            artifacts.append(frame_artifact)
            frame_paths[candidate_id] = frame_artifact.path

        incident_artifact_ids: dict[str, str] = {}
        incident_proofs: dict[str, ProofIncident] = {}
        for incident in incidents:
            source = Path(incident.artifact_path)
            source_kind, source_media_type = _media_type(source)
            incident_artifact_id = _next_artifact_id(artifacts)
            safe_incident = ProofIncident(
                incident_id=incident.incident_id,
                kind=incident.kind,
                attempt_id=incident.attempt_id,
                observed_code=_sanitize_public_text(incident.observed_code, label=incident.incident_id),
                observed_status=_sanitize_public_text(incident.observed_status, label=incident.incident_id),
                external_write_count=incident.external_write_count,
                summary=f"Persisted {incident.kind.replace('_', ' ')} proof for attempt {incident.attempt_id}.",
                artifact_path=incident.artifact_path,
            )
            incident_proofs[incident.incident_id] = safe_incident
            evidence_relative = (
                f"incidents/{incident.incident_id}-evidence{source.suffix.casefold() or '.bin'}"
                if source_kind in {"image", "gif"}
                else f"incidents/{incident.incident_id}-evidence.json"
            )
            if source_kind in {"image", "gif"}:
                evidence_artifact = _copy_asset_with_relative_path(
                    source,
                    stage,
                    evidence_relative,
                    source_sha256=ledger.source_sha256,
                    attempt_ids=(incident.attempt_id,),
                    expected_kind=source_kind,
                    artifact_id=incident_artifact_id,
                )
            else:
                _require_file(source, label="incident evidence")
                try:
                    raw_text = source.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as error:
                    raise ProofPackageError("incident evidence text is not valid UTF-8") from error
                if source.suffix.casefold() == ".json":
                    public_evidence = _sanitize_public_json(raw_text, label="incident evidence") + "\n"
                else:
                    _sanitize_public_text(raw_text, label="incident evidence")
                    public_evidence = json.dumps(
                        {
                            "format": "sanitized-text-evidence",
                            "source_artifact_sha256": _sha256_bytes(raw_text.encode("utf-8")),
                            "incident_id": safe_incident.incident_id,
                            "attempt_id": safe_incident.attempt_id,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                evidence_artifact = _write_artifact(
                    stage,
                    evidence_relative,
                    public_evidence.encode("utf-8"),
                    kind="json",
                    media_type="application/json",
                    source_sha256=ledger.source_sha256,
                    attempt_ids=(incident.attempt_id,),
                    artifact_id=incident_artifact_id,
                )
            artifacts.append(evidence_artifact)
            incident_artifact_ids[incident.incident_id] = incident_artifact_id

        safe_removed_waits = tuple(
            RemovedWait(
                label=_sanitize_public_text(wait.label, label="GIF removed wait"),
                elapsed_seconds=wait.elapsed_seconds,
            )
            for wait in gif_timeline.removed_waits
        )
        safe_gif_events = tuple(
            {
                "label": _sanitize_public_text(event.label, label="GIF event"),
                "attempt_id": _sanitize_public_text(event.attempt_id, label="GIF event attempt"),
                "elapsed_seconds": event.elapsed_seconds,
                "description": _sanitize_public_text(event.description, label="GIF event description"),
            }
            for event in gif_timeline.events
        )
        timeline_artifact_id = _next_artifact_id(artifacts)
        timeline_payload = {
            "final_attempt_id": final_attempt_id,
            "events": list(safe_gif_events),
            "removed_waits": [
                {"label": wait.display_label, "elapsed_seconds": wait.elapsed_seconds}
                for wait in safe_removed_waits
            ],
            "recorded_run": gif_timeline.recorded_run,
            "watcher_used": gif_timeline.watcher_used,
            "replay_fabricated_output": gif_timeline.replay_fabricated_output,
        }
        timeline_artifact = _write_artifact(
            stage,
            "media/third-run-timeline.json",
            json.dumps(timeline_payload, indent=2, sort_keys=True).encode("utf-8"),
            kind="json",
            media_type="application/json",
            source_sha256=ledger.source_sha256,
            attempt_ids=(final_attempt_id,),
            artifact_id=timeline_artifact_id,
        )
        artifacts.append(timeline_artifact)

        safe_epochs = tuple(
            FingerprintEpoch(
                fingerprint=epoch.analysis_fingerprint,
                current=current,
                source_sha256=epoch.source_sha256,
                fingerprint_inputs=_safe_fingerprint_inputs(epoch),
            )
            for epoch, current in epochs
        )
        safe_ledger = {
            "version": 1,
            "source_sha256": ledger.source_sha256,
            "current_fingerprint": ledger.analysis_fingerprint,
            "acceptance": _safe_acceptance(acceptance),
            "epochs": [
                _safe_epoch(epoch, current=current, frame_paths=frame_paths if current else {})
                for epoch, current in epochs
            ],
        }
        ledger_artifact_id = _next_artifact_id(artifacts)
        ledger_artifact = _write_artifact(
            stage,
            "ledger.json",
            json.dumps(safe_ledger, indent=2, sort_keys=True).encode("utf-8"),
            kind="json",
            media_type="application/json",
            source_sha256=ledger.source_sha256,
            attempt_ids=tuple(sorted(all_attempt_ids)),
            artifact_id=ledger_artifact_id,
        )
        artifacts.append(ledger_artifact)

        report_artifact_id = _next_artifact_id(artifacts)
        report_artifact = _write_artifact(
            stage,
            "deterministic-report.json",
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True).encode("utf-8"),
            kind="json",
            media_type="application/json",
            source_sha256=ledger.source_sha256,
            attempt_ids=(final_attempt_id,),
            artifact_id=report_artifact_id,
        )
        artifacts.append(report_artifact)

        incident_json_artifacts: dict[str, str] = {}
        for incident in incidents:
            json_artifact_id = _next_artifact_id(artifacts)
            proof_relative = f"incidents/{incident.incident_id}.json"
            proof_artifact = _write_artifact(
                stage,
                proof_relative,
                _incident_json(incident_proofs[incident.incident_id], incident_artifact_ids[incident.incident_id]),
                kind="json",
                media_type="application/json",
                source_sha256=ledger.source_sha256,
                attempt_ids=(incident.attempt_id,),
                artifact_id=json_artifact_id,
            )
            artifacts.append(proof_artifact)
            incident_json_artifacts[incident.incident_id] = json_artifact_id

        def claim(
            claim_id: str,
            text: str,
            check: str,
            expected: str,
            observed: str,
            artifact_ids: Sequence[str],
            attempt_ids: Sequence[str],
        ) -> ProofClaim:
            return ProofClaim(
                claim_id=claim_id,
                claim=text,
                executable_check=check,
                expected=expected,
                observed=observed,
                status="passed",
                artifact_ids=tuple(artifact_ids),
                attempt_ids=tuple(attempt_ids),
            )

        public_incidents = tuple(incident_proofs.values())
        background = next(item for item in public_incidents if item.kind == "background_interaction_rejection")
        zero_write_incidents = tuple(
            item for item in public_incidents if item.kind in {"incomplete_analysis", "uncertain_write"}
        )
        attempts_by_id = _attempt_by_id(ledger)
        qualified_safe_attempts = tuple(
            _safe_attempt(attempts_by_id[attempt_id], fingerprint=ledger.analysis_fingerprint, frame_paths={})
            for attempt_id in acceptance.qualified_attempt_ids
        )
        trust_claims = (
            claim(
                "claim_request_trust",
                "All qualifying live attempts passed the exact-interaction completion, processing-pair and usage trust gate.",
                "_has_verified_analysis_proof for every AcceptanceRecord.qualified_attempt_id",
                "verified status; exact interaction ID persisted; completed retrieval; processing pairs; usage and timings",
                (
                    f"{sum(item['interaction_verified'] is True for item in qualified_safe_attempts)}/"
                    f"{len(qualified_safe_attempts)} interaction_verified; processing pairs "
                    f"{[item['processing_pair_count'] for item in qualified_safe_attempts]}; usage recorded for every attempt"
                ),
                (image_slots[2].artifact_id, ledger_artifact_id),
                acceptance.qualified_attempt_ids,
            ),
        )
        claim_index = (
            claim(
                "claim_acceptance",
                "The frozen acceptance cycle passed with three distinct qualifying attempts.",
                "AcceptanceRecord.passed and len(qualified_attempt_ids) == 3",
                "passed; exactly three distinct attempts",
                f"passed; attempts {', '.join(acceptance.qualified_attempt_ids)}",
                (image_slots[3].artifact_id, ledger_artifact_id),
                acceptance.qualified_attempt_ids,
            ),
            *trust_claims,
            claim(
                "claim_fingerprint",
                "Every settled behavior fingerprint is preserved and the current epoch is identified.",
                "RunLedger.load for current and ledger-*.json archives",
                "all settled ledgers validate and hashes are unique",
                f"{len(safe_epochs)} validated fingerprint epochs",
                (image_slots[8].artifact_id, ledger_artifact_id),
                tuple(sorted(all_attempt_ids)),
            ),
            claim(
                "claim_deterministic_score",
                "The final persisted policy result reproduces the deterministic fixture score.",
                "score_policy_result(final_policy, ground_truth).passed",
                "passed with all six authored cases matched",
                f"passed; matched {len(report.matched_case_ids)} cases and {report.actionable_result_count} actionable routes",
                (image_slots[1].artifact_id, report_artifact_id),
                (final_attempt_id,),
            ),
            claim(
                "claim_evidence_and_review",
                "The final run contains both required Evidence Frames and the reviewed routes.",
                "EvidenceFrameRecord validation and final write/Approval metadata",
                "required Evidence Frames extracted and routes reviewed",
                f"{len(frame_paths)} Evidence Frames copied; {len(ledger.approvals)} Approvals persisted",
                (image_slots[5].artifact_id, image_slots[6].artifact_id, ledger_artifact_id),
                (final_attempt_id,),
            ),
            claim(
                "claim_final_issues",
                "The final run has one persisted written Issue Record for every actionable route.",
                "latest_write_attempts(final_attempt) == written and IssueRecord metadata matches Approval",
                f"{len(actionable)} verified written Issue outcomes",
                f"{written_issue_count} written Issue Records persisted",
                (image_slots[7].artifact_id, ledger_artifact_id),
                (final_attempt_id,),
            ),
            claim(
                "claim_background_rejection",
                "The public proof includes the recorded rejection of a background interaction request.",
                "incident.kind == background_interaction_rejection and observed_status is rejected/failed",
                "rejected before any external write",
                f"{background.observed_status}; external writes {background.external_write_count}",
                (incident_json_artifacts[background.incident_id], incident_artifact_ids[background.incident_id]),
                (background.attempt_id,),
            ),
            claim(
                "claim_zero_unsafe_writes",
                "Persisted incomplete or uncertain safety incidents demonstrate zero unsafe writes.",
                "incident.external_write_count == 0",
                "zero external writes",
                "; ".join(
                    f"{incident.kind}; external writes {incident.external_write_count}"
                    for incident in zero_write_incidents
                ),
                tuple(
                    artifact_id
                    for incident in zero_write_incidents
                    for artifact_id in (
                        incident_json_artifacts[incident.incident_id],
                        incident_artifact_ids[incident.incident_id],
                    )
                ),
                tuple(incident.attempt_id for incident in zero_write_incidents),
            ),
            claim(
                "claim_image_sequence",
                "The public proof contains the required nine-image sequence in order.",
                "tuple(image.label for image in image_sequence) == IMAGE_SEQUENCE",
                "nine required labels in fixed order",
                f"{len(image_slots)} labels in the required order",
                tuple(item.artifact_id for item in image_slots),
                (final_attempt_id,),
            ),
            claim(
                "claim_gif_timeline",
                "The edited GIF is a 20-30 second recorded-run summary, not a watcher or fabricated replay.",
                "duration in range and GifTimeline event/flag validation",
                "20-30 seconds; four ordered events; no watcher; no fabricated replay",
                f"{duration:.2f} seconds; {len(gif_timeline.events)} ordered events; recorded run",
                (gif_artifact_id, timeline_artifact_id),
                (final_attempt_id,),
            ),
            claim(
                "claim_public_sanitization",
                "Every staged public artifact passed the credential, personal-data, and private-path audit.",
                "_audit_public_bytes applied before each artifact is written or copied",
                "no sensitive bytes in public artifacts",
                f"{len(artifacts)} staged artifacts audited",
                tuple(item.artifact_id for item in artifacts),
                tuple(sorted(all_attempt_ids)),
            ),
            claim(
                "claim_bundle_contents",
                "The frozen bundle contains the required screenshots, media, reports, sanitized ledger, incidents, and claim index.",
                "ProofPackage validation plus staged artifact inventory",
                "nine images, GIF/timeline, Evidence Frames, incidents, ledger, report, and claim index",
                f"{len(image_slots)} images; {len(frame_paths)} Evidence Frames; {len(artifacts)} pre-manifest artifacts",
                tuple(item.artifact_id for item in artifacts),
                tuple(sorted(all_attempt_ids)),
            ),
        )
        claim_index_artifact_id = _next_artifact_id(artifacts)
        claim_index_artifact = _write_artifact(
            stage,
            "claim-index.json",
            json.dumps([item.model_dump(mode="json") for item in claim_index], indent=2, sort_keys=True).encode("utf-8"),
            kind="json",
            media_type="application/json",
            source_sha256=ledger.source_sha256,
            attempt_ids=tuple(sorted(all_attempt_ids)),
            artifact_id=claim_index_artifact_id,
        )
        artifacts.append(claim_index_artifact)

        package = ProofPackage(
            source_sha256=ledger.source_sha256,
            current_fingerprint=ledger.analysis_fingerprint,
            settled_fingerprint_hashes=tuple(epoch.fingerprint for epoch in safe_epochs),
            fingerprint_epochs=safe_epochs,
            acceptance_attempt_ids=tuple(acceptance.qualified_attempt_ids),
            final_attempt_id=final_attempt_id,
            deterministic_report=report,
            sanitized_ledger_path="ledger.json",
            sanitized_ledger_artifact_id=ledger_artifact_id,
            claim_index_path="claim-index.json",
            claim_index=claim_index,
            image_sequence=tuple(image_slots),
            gif=GifProof(
                artifact_id=gif_artifact_id,
                timeline_artifact_id=timeline_artifact_id,
                duration_seconds=duration,
                final_attempt_id=final_attempt_id,
                removed_waits=safe_removed_waits,
                watcher_used=gif_timeline.watcher_used,
                replay_fabricated_output=gif_timeline.replay_fabricated_output,
            ),
            incidents=public_incidents,
            artifacts=tuple(artifacts),
        )
        manifest = package.model_dump(mode="json")
        _audit_public_bytes(
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            label="proof manifest",
        )
        atomic_write_json(stage / "manifest.json", manifest, prefix="proof-")
        os.replace(stage, bundle_dir)
        return package
    except ProofPackageError:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    except (OSError, TypeError, ValueError, ValidationError) as error:
        shutil.rmtree(stage, ignore_errors=True)
        raise ProofPackageError("could not freeze the proof bundle") from error
