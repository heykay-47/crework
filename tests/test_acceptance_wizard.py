from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_recorded_acceptance_run_uses_frozen_container_defaults() -> None:
    script = (ROOT / ".agent" / "wizards" / "acceptance-run.sh").read_text(encoding="utf-8")

    # `run` resolves these paths from the canonical fixture mounted at
    # /app/fixtures. Its frozen-cycle guard intentionally rejects prompt and
    # context overrides, so the recording wrapper must not pass any of them.
    assert "--prompt" not in script
    assert "--context" not in script
    assert "--ground-truth" not in script
