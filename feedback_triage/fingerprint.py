import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_digest(path: Path) -> str:
    if not path.is_file():
        return _sha256(b"missing")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.name).encode())
        digest.update(b"\0")
        digest.update(_file_digest(path).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def build_analysis_fingerprint_inputs(
    source_sha256: str,
    *,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    project_context: str,
    fixture_version: str | None = None,
    ground_truth_sha256: str | None = None,
) -> dict[str, str]:
    package_dir = Path(__file__).parent
    repository_dir = package_dir.parent
    implementation_files = [
        *package_dir.glob("*.py"),
        repository_dir / "main.py",
        repository_dir / "scripts" / "make-canonical-recording.sh",
    ]
    dependency_files = [repository_dir / "uv.lock", repository_dir / "pyproject.toml", repository_dir / "Dockerfile"]
    schema_json = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    return {
        "source": source_sha256,
        "model": model,
        "prompt": _sha256(prompt.encode()),
        "schema": _sha256(schema_json),
        "context": _sha256(project_context.encode()),
        "policy": _file_digest(package_dir / "policy.py"),
        "dependency": _files_digest(dependency_files),
        "implementation": _files_digest(implementation_files),
        "fixture_version": fixture_version or "",
        "ground_truth": ground_truth_sha256 or "",
    }


def fingerprint_inputs_digest(inputs: dict[str, str]) -> str:
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(encoded)
