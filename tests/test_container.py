from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _docker(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_container_smoke(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("container smoke test skipped: Docker CLI is unavailable")
    daemon = _docker("info", timeout=20.0)
    if daemon.returncode != 0:
        detail = daemon.stderr.strip().splitlines()[-1] if daemon.stderr.strip() else "Docker daemon is unavailable"
        pytest.skip(f"container smoke test skipped: {detail}")

    input_root = tmp_path / "input"
    context_root = tmp_path / "context"
    output_root = tmp_path / "output"
    input_root.mkdir()
    context_root.mkdir()
    output_root.mkdir()
    (input_root / "read-only-fixture.txt").write_text("fixture", encoding="utf-8")
    (context_root / "CONTEXT.md").write_text("container smoke context", encoding="utf-8")
    image = "client-feedback-triage:issue-21-smoke"
    build = _docker("build", "-t", image, ".", timeout=300.0)
    assert build.returncode == 0, build.stderr[-2000:]

    port = _free_port()
    run = _docker(
        "run",
        "--detach",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "GEMINI_API_KEY",
        "--publish",
        f"127.0.0.1:{port}:8000",
        "--mount",
        f"type=bind,src={input_root},dst=/input,readonly",
        "--mount",
        f"type=bind,src={context_root},dst=/context,readonly",
        "--mount",
        f"type=bind,src={output_root},dst=/output",
        image,
        "web",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        timeout=30.0,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    container_id = run.stdout.strip()
    try:
        base_url = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if httpx.get(f"{base_url}/api/health", timeout=0.5).json() == {"status": "ok"}:
                    break
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.25)
        else:
            logs = _docker("logs", container_id, timeout=20.0)
            raise AssertionError(f"container did not become healthy: {logs.stdout[-1000:]}{logs.stderr[-1000:]}")

        page = httpx.get(f"{base_url}/", timeout=5.0)
        assert page.status_code == 200
        assert "Feedback Recording review" in page.text

        mounts = _docker(
            "exec",
            container_id,
            "sh",
            "-c",
            "test -r /input/read-only-fixture.txt && test ! -w /input/read-only-fixture.txt && test -r /context/CONTEXT.md && test ! -w /context/CONTEXT.md && printf smoke > /output/container-smoke.txt",
            timeout=20.0,
        )
        assert mounts.returncode == 0, mounts.stderr[-1000:]
        assert (output_root / "container-smoke.txt").read_text(encoding="utf-8") == "smoke"
    finally:
        _docker("stop", container_id, timeout=30.0)
