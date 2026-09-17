import json
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Sequence
from typing import Any

import pytest

from feedback_triage.analyze import AnalysisFailed, analyze_recording
from feedback_triage.gemini_video import FilesAPI, InteractionAPI, run_stored_stream
from tests.test_completion_trust import VALID_OUTPUT


class FakeFiles:
    def upload(self, *, file: str) -> object:
        return SimpleNamespace(state=SimpleNamespace(name="ACTIVE"), uri="gemini://video", mime_type="video/mp4")

    def get(self, *, name: str) -> object:
        raise AssertionError("active upload must not be polled")


class FakeInteractions:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.create_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> list[object]:
        self.create_kwargs = kwargs
        return self.events

    def get(self, *, id: str) -> object:
        return SimpleNamespace(
            status="completed",
            output_text=VALID_OUTPUT,
            steps=[
                SimpleNamespace(type="processing_call", id="segment-1"),
                SimpleNamespace(type="processing_result", call_id="segment-1"),
            ],
        )


class FakeClient:
    files: FilesAPI
    interactions: InteractionAPI

    def __init__(self, events: Sequence[object]) -> None:
        self.files = FakeFiles()
        self.interactions = FakeInteractions(list(events))


def test_stream_deltas_cannot_arrive_before_interaction_id_is_persisted(tmp_path: Path) -> None:
    client = FakeClient([SimpleNamespace(event_type="step.delta")])

    with pytest.raises(Exception, match="before interaction ID was persisted"):
        run_stored_stream(client, tmp_path / "feedback.mp4", on_created=lambda _: None, on_diagnostic=lambda _: None)


def test_stream_error_is_terminal_after_interaction_id_is_persisted(tmp_path: Path) -> None:
    events = [
        SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id="interaction-123")),
        SimpleNamespace(event_type="error", error=SimpleNamespace(code="invalid_request", message="bad input")),
    ]

    with pytest.raises(Exception, match="stream error invalid_request: bad input"):
        run_stored_stream(FakeClient(events), tmp_path / "feedback.mp4", on_created=lambda _: None, on_diagnostic=lambda _: None)


def test_invalid_input_is_recorded_without_starting_gemini(tmp_path: Path) -> None:
    video = tmp_path / "feedback.mp4"
    video.write_text("not video")

    with pytest.raises(AnalysisFailed) as failure:
        analyze_recording(video, tmp_path / "output", FakeClient([]))

    assert failure.value.code == "invalid_input"
    ledgers = list((tmp_path / "output").glob("*/ledger.json"))
    saved = json.loads(ledgers[0].read_text())
    assert saved["attempts"][0]["failure"]["code"] == "invalid_input"


def test_missing_input_is_recorded_without_starting_gemini(tmp_path: Path) -> None:
    with pytest.raises(AnalysisFailed) as failure:
        analyze_recording(tmp_path / "missing.mp4", tmp_path / "output", FakeClient([]))

    assert failure.value.code == "invalid_input"
    ledgers = list((tmp_path / "output").glob("*/ledger.json"))
    assert len(ledgers) == 1
    saved = json.loads(ledgers[0].read_text())
    assert saved["attempts"][0]["failure"]["code"] == "invalid_input"


def test_completed_stored_stream_becomes_verified_analysis(tmp_path: Path) -> None:
    events = [
        SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id="interaction-123")),
        SimpleNamespace(event_type="step.delta"),
        SimpleNamespace(event_type="interaction.completed"),
    ]
    persisted: list[str] = []
    client = FakeClient(events)

    verified = run_stored_stream(
        client,
        tmp_path / "feedback.mp4",
        on_created=persisted.append,
        on_diagnostic=lambda _: None,
    )

    assert persisted == ["interaction-123"]
    assert verified.analysis.schema_version == "1.0"
    interactions = client.interactions
    assert isinstance(interactions, FakeInteractions)
    assert interactions.create_kwargs is not None
    assert interactions.create_kwargs["response_format"]["text"]["mime_type"] == "application/json"
