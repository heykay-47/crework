"""Best-effort diagnostic timing callbacks."""

from collections.abc import Callable


TimingCallback = Callable[[str, float], None]


def report_timing(callback: TimingCallback | None, name: str, seconds: float) -> None:
    if callback is None:
        return
    try:
        callback(name, seconds)
    except Exception:
        # Diagnostics must not replace a trust, review, or write result.
        return
