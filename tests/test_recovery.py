import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from feedback_triage.analyze import AnalysisFailed, analysis_fingerprint, analysis_fingerprint_inputs, analyze_recording, file_sha256
from feedback_triage.gemini_video import FilesAPI, InteractionAPI, retrieve_verified_interaction
from feedback_triage.input_video import VideoInfo
from feedback_triage.ledger import RunLedger
from tests.test_completion_trust import VALID_OUTPUT


def completed_interaction(output: str = VALID_OUTPUT) -> object:
    return SimpleNamespace(
        status="completed",
        output_text=output,
        steps=[
            SimpleNamespace(type="processing_call", id="segment-1"),
            SimpleNamespace(type="processing_result", call_id="segment-1"),
        ],
    )


class RecoveryInteractions:
    def __init__(self, retrieved: object) -> None:
        self.retrieved = retrieved
        self.get_ids: list[str] = []
        self.create_calls = 0

    def get(self, *, id: str) -> object:
        self.get_ids.append(id)
        return self.retrieved

    def create(self, **kwargs: Any) -> list[object]:
        self.create_calls += 1
        raise AssertionError("recovery must not create another interaction")


class NoUploadFiles:
    def upload(self, *, file: str) -> object:
        raise AssertionError("recovery must not upload again")

    def get(self, *, name: str) -> object:
        raise AssertionError("recovery must not poll an upload")


class RecoveryClient:
    interactions: InteractionAPI
    files: FilesAPI

    def __init__(self, retrieved: object) -> None:
        self.interactions = RecoveryInteractions(retrieved)
        self.files = NoUploadFiles()


def prepare_video(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    video = tmp_path / "feedback.mp4"
    video.write_bytes(b"owned synthetic recording")
    monkeypatch.setattr("feedback_triage.analyze.probe_video", lambda path: VideoInfo(path=path, duration_seconds=12.5))
    return video


def interrupted_ledger(video: Path, output: Path, interaction_id: str = "interaction-123") -> RunLedger:
    source = file_sha256(video)
    ledger = RunLedger.create(
        output,
        source_sha256=source,
        fingerprint=analysis_fingerprint(source),
        fingerprint_inputs=analysis_fingerprint_inputs(source),
    )
    attempt_id = ledger.start_attempt()
    ledger.record_interaction_created(attempt_id, interaction_id)
    return ledger


def test_restart_reconciles_persisted_interaction_without_reexecution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    interrupted_ledger(video, tmp_path / "output")
    interactions = RecoveryInteractions(completed_interaction())
    client = RecoveryClient(completed_interaction())
    client.interactions = interactions

    _, verified, ledger = analyze_recording(video, tmp_path / "output", client)

    assert interactions.get_ids == ["interaction-123"]
    assert interactions.create_calls == 0
    assert verified.analysis.observations[0].observation_id == "obs_001"
    saved = json.loads(ledger.path.read_text())
    assert len(saved["attempts"]) == 1
    assert saved["attempts"][0]["status"] == "verified"


def test_normal_rerun_reuses_verified_analysis_without_a_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    ledger = interrupted_ledger(video, tmp_path / "output")
    attempt_id = json.loads(ledger.path.read_text())["attempts"][0]["attempt_id"]
    ledger.complete(attempt_id, json.loads(VALID_OUTPUT), processing_pair_count=1)

    _, verified, rerun_ledger = analyze_recording(video, tmp_path / "output")

    assert verified.processing_pair_count == 1
    assert len(json.loads(rerun_ledger.path.read_text())["attempts"]) == 1


def test_unreconciled_attempt_blocks_normal_rerun(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    source = file_sha256(video)
    ledger = RunLedger.create(
        tmp_path / "output",
        source_sha256=source,
        fingerprint=analysis_fingerprint(source),
        fingerprint_inputs=analysis_fingerprint_inputs(source),
    )
    ledger.start_attempt()

    with pytest.raises(AnalysisFailed) as failure:
        analyze_recording(video, tmp_path / "output", RecoveryClient(completed_interaction()))

    assert failure.value.code == "attempt_unreconciled"


def test_normal_rerun_does_not_replace_a_reconciled_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    ledger = interrupted_ledger(video, tmp_path / "output")
    attempt_id = json.loads(ledger.path.read_text())["attempts"][0]["attempt_id"]
    ledger.fail(attempt_id, code="interaction_terminal", detail="retrieved interaction status is failed")
    client = RecoveryClient(completed_interaction())

    with pytest.raises(AnalysisFailed) as failure:
        analyze_recording(video, tmp_path / "output", client)

    assert failure.value.code == "interaction_terminal"
    assert len(json.loads(ledger.path.read_text())["attempts"]) == 1


def test_reanalyze_appends_but_never_combines_attempt_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    ledger = interrupted_ledger(video, tmp_path / "output")
    first_attempt = json.loads(ledger.path.read_text())["attempts"][0]["attempt_id"]
    ledger.complete(first_attempt, json.loads(VALID_OUTPUT), processing_pair_count=1)

    replacement = json.loads(VALID_OUTPUT)
    replacement["observations"][0]["observation_id"] = "obs_002"

    class ReplacementInteractions(RecoveryInteractions):
        def create(self, **kwargs: Any) -> list[object]:
            self.create_calls += 1
            return [
                SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id="interaction-456")),
                SimpleNamespace(event_type="interaction.completed"),
            ]

    class ReplacementFiles(NoUploadFiles):
        def upload(self, *, file: str) -> object:
            return SimpleNamespace(state=SimpleNamespace(name="ACTIVE"), uri="gemini://video", mime_type="video/mp4")

    client = RecoveryClient(completed_interaction(json.dumps(replacement)))
    client.interactions = ReplacementInteractions(completed_interaction(json.dumps(replacement)))
    client.files = ReplacementFiles()

    _, verified, rerun_ledger = analyze_recording(video, tmp_path / "output", client, reanalyze=True)

    assert verified.analysis.observations[0].observation_id == "obs_002"
    saved = json.loads(rerun_ledger.path.read_text())
    assert len(saved["attempts"]) == 2
    assert saved["attempts"][0]["analysis"]["observations"][0]["observation_id"] == "obs_001"
    assert saved["attempts"][1]["analysis"]["observations"][0]["observation_id"] == "obs_002"


def test_normal_rerun_reconciles_latest_attempt_instead_of_returning_stale_verified_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    ledger = interrupted_ledger(video, tmp_path / "output", "interaction-old")
    first = ledger.attempts[0]["attempt_id"]
    ledger.complete(str(first), json.loads(VALID_OUTPUT), processing_pair_count=1)
    second = ledger.start_attempt()
    ledger.record_interaction_created(second, "interaction-new")
    replacement = json.loads(VALID_OUTPUT)
    replacement["observations"][0]["observation_id"] = "obs_002"
    client = RecoveryClient(completed_interaction(json.dumps(replacement)))

    _, verified, _ = analyze_recording(video, tmp_path / "output", client)

    assert verified.analysis.observations[0].observation_id == "obs_002"


def test_reanalyze_can_replace_a_newly_reconciled_terminal_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = prepare_video(monkeypatch, tmp_path)
    interrupted_ledger(video, tmp_path / "output")

    class TerminalThenReplacement(RecoveryInteractions):
        def get(self, *, id: str) -> object:
            if id == "interaction-123":
                return SimpleNamespace(status="failed")
            return self.retrieved

        def create(self, **kwargs: Any) -> list[object]:
            return [
                SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id="interaction-new")),
                SimpleNamespace(event_type="step.delta", delta="fragment from replacement attempt"),
                SimpleNamespace(event_type="interaction.completed"),
            ]

    class ActiveFiles(NoUploadFiles):
        def upload(self, *, file: str) -> object:
            return SimpleNamespace(state=SimpleNamespace(name="ACTIVE"), uri="gemini://video", mime_type="video/mp4")

    client = RecoveryClient(completed_interaction())
    client.interactions = TerminalThenReplacement(completed_interaction())
    client.files = ActiveFiles()

    _, verified, ledger = analyze_recording(video, tmp_path / "output", client, reanalyze=True)

    assert verified.analysis.observations[0].observation_id == "obs_001"
    assert [attempt["status"] for attempt in ledger.attempts] == ["failed", "verified"]
    assert "fragment" not in json.dumps(ledger.attempts)


def test_interrupted_stream_remains_recoverable_on_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = prepare_video(monkeypatch, tmp_path)

    class InterruptedFiles(NoUploadFiles):
        def upload(self, *, file: str) -> object:
            return SimpleNamespace(state=SimpleNamespace(name="ACTIVE"), uri="gemini://video", mime_type="video/mp4")

    class InterruptedInteractions(RecoveryInteractions):
        def create(self, **kwargs: Any) -> list[object]:
            return [
                SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id="interaction-123")),
                SimpleNamespace(event_type="step.delta", delta="untrusted fragment"),
            ]

    interrupted = RecoveryClient(completed_interaction())
    interrupted.files = InterruptedFiles()
    interrupted.interactions = InterruptedInteractions(completed_interaction())

    with pytest.raises(AnalysisFailed) as failure:
        analyze_recording(video, tmp_path / "output", interrupted)
    assert failure.value.code == "interaction_timeout"

    recovered = RecoveryClient(completed_interaction())
    _, verified, ledger = analyze_recording(video, tmp_path / "output", recovered)

    assert verified.analysis.observations[0].observation_id == "obs_001"
    saved = json.loads(ledger.path.read_text())
    assert len(saved["attempts"]) == 1
    assert "untrusted fragment" not in json.dumps(saved)


def test_retrieval_retry_honors_retry_after_and_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    class RetryableError(RuntimeError):
        status_code = 503
        headers = {"Retry-After": "0.25"}

    interactions = RecoveryInteractions(completed_interaction())
    original_get = interactions.get
    failures = iter([RetryableError("busy"), RetryableError("busy")])

    def flaky_get(*, id: str) -> object:
        try:
            raise next(failures)
        except StopIteration:
            return original_get(id=id)

    interactions.get = flaky_get  # type: ignore[method-assign]
    client = RecoveryClient(completed_interaction())
    client.interactions = interactions
    monkeypatch.setattr("feedback_triage.gemini_video.time.sleep", sleeps.append)

    verified = retrieve_verified_interaction(client, "interaction-123")

    assert verified.processing_pair_count == 1
    assert sleeps == [0.25, 0.25]
