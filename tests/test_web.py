from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from feedback_triage import web
from feedback_triage.analyze import analysis_fingerprint, analysis_fingerprint_inputs, file_sha256
from feedback_triage.gemini_video import PROMPT
from feedback_triage.github import GitHubApiError, GitHubIssue
from feedback_triage.input_video import VideoInfo
from feedback_triage.ledger import RunLedger
from feedback_triage.models import AnalysisResult, EvidenceFrameRecord, RoutedResult, VerifiedAnalysis, analysis_to_wire
from tests.test_completion_trust import VALID_ANALYSIS_JSON


def routed(candidate_id: str, route: str, *, evidence_count: int = 1) -> RoutedResult:
    evidence = [
        {
            "start_seconds": float(index + 1),
            "end_seconds": float(index + 2),
            "keyframe_seconds": float(index + 1.5),
            "client_quote": "Please make this clearer.",
            "visual_observation": "The control is hard to distinguish.",
        }
        for index in range(evidence_count)
    ]
    return RoutedResult.model_validate(
        {
            "route": route,
            "candidate_id": candidate_id,
            "topic_key": candidate_id,
            "type": "change_request",
            "intent": "explicit_change",
            "title": f"Review {candidate_id}",
            "component": "hero",
            "summary": "Make the requested change.",
            "requested_outcome": "A clearer result.",
            "acceptance_criteria": ["The reviewed result is clear."],
            "clarification_question": None,
            "confidence": "high",
            "evidence": evidence,
            "evidence_frame_seconds": 1.5,
            "rationale": "The recording contains an actionable request.",
            "approval_eligible": route == "candidate",
            "visually_inferred": route == "manual_review",
            "reason_code": "low_confidence" if route == "withheld_result" else None,
        }
    )


@dataclass
class FakeGateway:
    create_calls: int = 0

    def find_marker(self, destination_repository: str, marker: str) -> tuple[GitHubIssue, ...]:
        return ()

    def get_issue(self, destination_repository: str, issue_number: int) -> GitHubIssue:
        raise AssertionError("the fake has no adopted Issues")

    def create_issue(self, destination_repository: str, payload: Any) -> GitHubIssue:
        self.create_calls += 1
        return GitHubIssue(
            destination_repository=destination_repository,
            number=self.create_calls,
            title=payload.title,
            body=payload.body,
            html_url=f"https://github.com/{destination_repository}/issues/{self.create_calls}",
            state="open",
        )


class BrowserGeminiFiles:
    def upload(self, *, file: str) -> object:
        return SimpleNamespace(
            name="files/browser",
            uri="gemini://browser",
            mime_type="video/mp4",
            state=SimpleNamespace(name="ACTIVE"),
        )

    def get(self, *, name: str) -> object:
        return SimpleNamespace(name=name, state=SimpleNamespace(name="ACTIVE"))


class BrowserGeminiInteractions:
    def __init__(self, analysis: AnalysisResult) -> None:
        self.analysis = analysis
        self.created_ids: list[str] = []

    def create(self, **_: Any) -> Iterator[object]:
        interaction_id = f"browser-interaction-{len(self.created_ids) + 1}"
        self.created_ids.append(interaction_id)
        yield SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id=interaction_id))
        yield SimpleNamespace(event_type="interaction.completed")

    def get(self, *, id: str) -> object:
        return SimpleNamespace(
            status="completed",
            output_text=analysis_to_wire(self.analysis).model_dump_json(),
            steps=[
                SimpleNamespace(type="processing_call", id=f"{id}-processing"),
                SimpleNamespace(type="processing_result", call_id=f"{id}-processing"),
            ],
            usage_metadata=SimpleNamespace(total_token_count=12),
        )


class BrowserGeminiGateway:
    def __init__(self) -> None:
        analysis = AnalysisResult.model_validate_json(VALID_ANALYSIS_JSON)
        self.files = BrowserGeminiFiles()
        self.interactions = BrowserGeminiInteractions(analysis)


def install_fake_analysis(monkeypatch: pytest.MonkeyPatch, seen_clients: list[object] | None = None) -> None:
    def fake_probe(video: Path) -> VideoInfo:
        return VideoInfo(path=video, duration_seconds=42.0)

    def fake_triage(
        video: Path,
        output: Path,
        *,
        client: object | None = None,
        reanalyze: bool = False,
        on_stage: Any = None,
        **_: Any,
    ) -> tuple[VideoInfo, VerifiedAnalysis, RunLedger,]:
        if seen_clients is not None:
            seen_clients.append(client)
        source_sha256 = file_sha256(video)
        fingerprint_inputs = analysis_fingerprint_inputs(source_sha256, prompt=PROMPT, project_context="")
        ledger = RunLedger.create(
            output,
            source_sha256=source_sha256,
            fingerprint=analysis_fingerprint(source_sha256, prompt=PROMPT, project_context=""),
            fingerprint_inputs=fingerprint_inputs,
        )
        attempt_id = ledger.start_attempt()
        for stage in ("upload", "gemini_processing", "interaction_verification", "policy_evaluation", "evidence_frame_extraction"):
            if on_stage is not None:
                on_stage(stage)
        analysis = {"schema_version": "1.0", "video_summary": "A short review recording.", "observations": []}
        ledger.complete(attempt_id, analysis, processing_pair_count=2)
        candidates = (
            routed("cand_aaaaaaaaaaaaaaaa", "candidate", evidence_count=2),
            routed("cand_bbbbbbbbbbbbbbbb", "clarification_request"),
            routed("cand_cccccccccccccccc", "manual_review"),
            routed("cand_dddddddddddddddd", "withheld_result"),
        )
        ledger.record_policy_result(attempt_id, {"schema_version": "1.0", "results": [candidate.model_dump(mode="json") for candidate in candidates]})
        frame_path = output / source_sha256 / "evidence-frames" / "frame.png"
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        frame_path.write_bytes(b"png")
        ledger.record_evidence_frame(
            attempt_id,
            EvidenceFrameRecord(candidate_id=candidates[0].candidate_id, timestamp_seconds=1.5, status="extracted", path=str(frame_path)),
        )
        verified = VerifiedAnalysis.model_validate({"analysis": analysis, "processing_pair_count": 2, "gemini_usage": {"input_tokens": 10}})
        return VideoInfo(path=video, duration_seconds=42.0), verified, ledger

    monkeypatch.setattr(web, "probe_video", fake_probe)
    monkeypatch.setattr(web, "triage_recording", fake_triage)


def wait_for_status(client: TestClient, recording_id: str, status: str) -> dict[str, Any]:
    for _ in range(50):
        response = client.get(f"/api/recordings/{recording_id}")
        assert response.status_code == 200
        payload = response.json()
        if not isinstance(payload, dict):
            raise AssertionError("recording response must be an object")
        if payload["status"] == status:
            return cast(dict[str, Any], payload)
        time.sleep(0.01)
    raise AssertionError(f"recording did not reach {status}")


def test_browser_workflow_groups_seeks_approves_and_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen_clients: list[object] = []
    gemini_client = object()
    install_fake_analysis(monkeypatch, seen_clients)
    gateway = FakeGateway()
    settings = web.WebSettings(output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=gemini_client, github_gateway=gateway)) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        invalid = client.post("/api/recordings", files={"file": ("notes.txt", b"not video", "text/plain")})
        assert invalid.status_code == 415

        uploaded = client.post("/api/recordings", files={"file": ("walkthrough.mp4", b"video", "video/mp4")})
        assert uploaded.status_code == 201
        recording_id = uploaded.json()["recording_id"]
        assert uploaded.json()["duration_seconds"] == 42.0

        started = client.post(f"/api/recordings/{recording_id}/analyze")
        assert started.status_code == 202
        ready = wait_for_status(client, recording_id, "ready_for_review")
        assert all(stage["state"] == "completed" for stage in ready["stages"])
        assert [group["label"] for group in ready["groups"]] == ["Candidate", "Clarification Request", "Manual Review", "Withheld Result"]
        candidate = ready["groups"][0]["candidates"][0]
        assert len(candidate["evidence_spans"]) == 2
        assert candidate["evidence_spans"][0]["start_seconds"] == 1.0
        assert candidate["evidence_frame"]["status"] == "extracted"
        assert client.get(candidate["evidence_frame"]["url"]).status_code == 200

        preview = client.post(
            f"/api/recordings/{recording_id}/candidates/{candidate['candidate_id']}/approval-preview",
            json={"action": "approve", "changes": {"title": "Approved title"}},
        )
        assert preview.status_code == 200
        assert preview.json()["payload"]["title"] == "Approved title"

        reviewed = client.post(
            f"/api/recordings/{recording_id}/candidates/{candidate['candidate_id']}/review",
            json={"action": "approve", "changes": {"title": "Approved title"}},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["recording"]["groups"][0]["candidates"][0]["decision"] == "approved"
        assert client.post(
            f"/api/recordings/{recording_id}/candidates/cand_bbbbbbbbbbbbbbbb/review",
            json={"action": "approve"},
        ).status_code == 409

        published = client.post(
            f"/api/recordings/{recording_id}/publish",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )
        assert published.status_code == 200
        assert gateway.create_calls == 1
        assert published.json()["recording"]["issue_records"][0]["candidate_id"] == candidate["candidate_id"]
        assert published.json()["recording"]["status"] == "published"
    assert seen_clients == [gemini_client]


def test_browser_uses_injected_gemini_through_real_analysis_pipeline(tmp_path: Path) -> None:
    source = Path("fixtures/canonical/feedback-recording.mp4")
    gateway = BrowserGeminiGateway()
    settings = web.WebSettings(output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=gateway, github_gateway=FakeGateway())) as client:
        uploaded = client.post(
            "/api/recordings",
            files={"file": (source.name, source.read_bytes(), "video/mp4")},
        )
        assert uploaded.status_code == 201
        recording_id = uploaded.json()["recording_id"]
        assert client.post(f"/api/recordings/{recording_id}/analyze").status_code == 202
        ready = wait_for_status(client, recording_id, "ready_for_review")

        assert gateway.interactions.created_ids == ["browser-interaction-1"]
        assert ready["groups"][0]["candidates"]
        assert all(stage["state"] == "completed" for stage in ready["stages"])


def test_fingerprint_mismatch_has_an_explicit_reanalysis_recovery_path(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "stale.mp4"
    source.write_bytes(Path("fixtures/canonical/feedback-recording.mp4").read_bytes())
    source_sha256 = file_sha256(source)
    old_fingerprint = "a" * 64
    ledger = RunLedger.create(
        tmp_path,
        source_sha256=source_sha256,
        fingerprint=old_fingerprint,
        fingerprint_inputs={"old": "behavior"},
    )
    old_attempt = ledger.start_attempt()
    ledger.complete(old_attempt, {"schema_version": "1.0", "video_summary": "Old result.", "observations": []}, processing_pair_count=1)
    ledger.record_policy_result(
        old_attempt,
        {"schema_version": "1.0", "results": [routed("cand_aaaaaaaaaaaaaaaa", "candidate").model_dump(mode="json")]},
    )
    gateway = BrowserGeminiGateway()
    settings = web.WebSettings(input_root=input_root, output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=gateway, github_gateway=FakeGateway())) as client:
        stale = client.get("/api/recordings").json()
        recording_id = next(recording["recording_id"] for recording in stale if recording["recording_id"].startswith("ledger-"))
        assert client.get(f"/api/recordings/{recording_id}").json()["failure"]["code"] == "fingerprint_mismatch"
        assert client.post(f"/api/recordings/{recording_id}/reanalyze").status_code == 202
        ready = wait_for_status(client, recording_id, "ready_for_review")

        assert ready["groups"]
        assert (tmp_path / source_sha256 / f"ledger-{old_fingerprint}.json").exists()


def test_manual_review_requires_explicit_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_analysis(monkeypatch)
    settings = web.WebSettings(output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=object(), github_gateway=FakeGateway())) as client:
        uploaded = client.post("/api/recordings", files={"file": ("walkthrough.mp4", b"video", "video/mp4")})
        recording_id = uploaded.json()["recording_id"]
        client.post(f"/api/recordings/{recording_id}/analyze")
        ready = wait_for_status(client, recording_id, "ready_for_review")
        manual = next(candidate for group in ready["groups"] if group["key"] == "manual_review" for candidate in group["candidates"])

        blocked = client.post(
            f"/api/recordings/{recording_id}/candidates/{manual['candidate_id']}/approval-preview",
            json={"action": "approve"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "manual_review_confirmation_required"

        confirmed = client.post(
            f"/api/recordings/{recording_id}/candidates/{manual['candidate_id']}/approval-preview",
            json={"action": "approve", "manual_review_confirmed": True},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["manual_review_confirmed"] is True
        reviewed = client.post(
            f"/api/recordings/{recording_id}/candidates/{manual['candidate_id']}/review",
            json={"action": "approve", "manual_review_confirmed": True},
        )
        assert reviewed.status_code == 200
        published = client.post(
            f"/api/recordings/{recording_id}/publish",
            json={"candidate_ids": [manual["candidate_id"]]},
        )
        assert published.status_code == 200


def test_completed_input_ledger_is_opened_without_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "completed.mp4"
    source.write_bytes(b"completed recording")
    source_sha256 = file_sha256(source)
    fingerprint_inputs = analysis_fingerprint_inputs(source_sha256, prompt=PROMPT, project_context="")
    ledger = RunLedger.create(
        tmp_path,
        source_sha256=source_sha256,
        fingerprint=analysis_fingerprint(source_sha256, prompt=PROMPT, project_context=""),
        fingerprint_inputs=fingerprint_inputs,
    )
    attempt_id = ledger.start_attempt()
    ledger.complete(
        attempt_id,
        {"schema_version": "1.0", "video_summary": "A completed recording.", "observations": []},
        processing_pair_count=1,
    )
    candidate = routed("cand_aaaaaaaaaaaaaaaa", "candidate")
    ledger.record_policy_result(attempt_id, {"schema_version": "1.0", "results": [candidate.model_dump(mode="json")]})

    monkeypatch.setattr(web, "probe_video", lambda video: VideoInfo(path=video, duration_seconds=12.0))
    monkeypatch.setattr(web, "triage_recording", lambda *_args, **_kwargs: pytest.fail("opened ledgers must not rerun analysis"))
    settings = web.WebSettings(input_root=input_root, output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=object(), github_gateway=FakeGateway())) as client:
        recordings = client.get("/api/recordings").json()
        opened = next(recording for recording in recordings if recording["recording_id"].startswith("ledger-"))
        assert opened["status"] == "ready_for_review"
        detail = client.get(f"/api/recordings/{opened['recording_id']}").json()
        assert detail["trust"]["verified"] is True
        assert client.get(detail["media_url"]).status_code == 200


def test_stale_completed_input_ledger_is_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "stale.mp4"
    source.write_bytes(b"stale recording")
    source_sha256 = file_sha256(source)
    ledger = RunLedger.create(
        tmp_path,
        source_sha256=source_sha256,
        fingerprint="f" * 64,
        fingerprint_inputs={"test": "stale"},
    )
    attempt_id = ledger.start_attempt()
    ledger.complete(
        attempt_id,
        {"schema_version": "1.0", "video_summary": "A stale recording.", "observations": []},
        processing_pair_count=1,
    )
    candidate = routed("cand_aaaaaaaaaaaaaaaa", "candidate")
    ledger.record_policy_result(attempt_id, {"schema_version": "1.0", "results": [candidate.model_dump(mode="json")]})

    monkeypatch.setattr(web, "probe_video", lambda video: VideoInfo(path=video, duration_seconds=12.0))
    settings = web.WebSettings(input_root=input_root, output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=object(), github_gateway=FakeGateway())) as client:
        recordings = client.get("/api/recordings").json()
        stale = next(recording for recording in recordings if recording["recording_id"].startswith("ledger-"))
        assert stale["status"] == "failed"
        assert stale["failure"]["code"] == "fingerprint_mismatch"
        detail = client.get(f"/api/recordings/{stale['recording_id']}").json()
        assert detail["groups"] == []


def test_superseded_input_ledger_does_not_reopen_an_older_policy_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    source = input_root / "superseded.mp4"
    source.write_bytes(b"superseded recording")
    source_sha256 = file_sha256(source)
    fingerprint_inputs = analysis_fingerprint_inputs(source_sha256, prompt=PROMPT, project_context="")
    ledger = RunLedger.create(
        tmp_path,
        source_sha256=source_sha256,
        fingerprint=analysis_fingerprint(source_sha256, prompt=PROMPT, project_context=""),
        fingerprint_inputs=fingerprint_inputs,
    )
    verified_attempt = ledger.start_attempt()
    ledger.complete(
        verified_attempt,
        {"schema_version": "1.0", "video_summary": "An older result.", "observations": []},
        processing_pair_count=1,
    )
    candidate = routed("cand_aaaaaaaaaaaaaaaa", "candidate")
    ledger.record_policy_result(verified_attempt, {"schema_version": "1.0", "results": [candidate.model_dump(mode="json")]})
    failed_attempt = ledger.start_attempt()
    ledger.fail(failed_attempt, code="output_invalid", detail="newer attempt failed")

    monkeypatch.setattr(web, "probe_video", lambda video: VideoInfo(path=video, duration_seconds=12.0))
    settings = web.WebSettings(input_root=input_root, output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=object(), github_gateway=FakeGateway())) as client:
        recordings = client.get("/api/recordings").json()
        assert not any(recording["recording_id"].startswith("ledger-") for recording in recordings)


def test_missing_gemini_configuration_is_visible_without_exposing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_analysis(monkeypatch)
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(key, raising=False)
    settings = web.WebSettings(output_root=tmp_path)
    with TestClient(web.create_app(settings)) as client:
        uploaded = client.post("/api/recordings", files={"file": ("walkthrough.mp4", b"video", "video/mp4")})
        recording_id = uploaded.json()["recording_id"]
        client.post(f"/api/recordings/{recording_id}/analyze")
        failed = wait_for_status(client, recording_id, "failed")
        assert failed["failure"]["code"] == "configuration_missing"
        assert "key" not in failed["failure"]["message"].lower()


def test_publish_failure_preserves_review_and_retry_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_analysis(monkeypatch)

    class FailingGateway(FakeGateway):
        def create_issue(self, destination_repository: str, payload: Any) -> GitHubIssue:
            raise GitHubApiError("GitHub rejected the request")

    settings = web.WebSettings(output_root=tmp_path, github_repository="Demo/Feedback")
    with TestClient(web.create_app(settings, gemini_client=object(), github_gateway=FailingGateway())) as client:
        uploaded = client.post("/api/recordings", files={"file": ("walkthrough.mp4", b"video", "video/mp4")})
        recording_id = uploaded.json()["recording_id"]
        client.post(f"/api/recordings/{recording_id}/analyze")
        ready = wait_for_status(client, recording_id, "ready_for_review")
        candidate = ready["groups"][0]["candidates"][0]
        assert client.post(
            f"/api/recordings/{recording_id}/candidates/{candidate['candidate_id']}/review",
            json={"action": "approve"},
        ).status_code == 200
        failed = client.post(
            f"/api/recordings/{recording_id}/publish",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )
        assert failed.status_code == 409
        assert failed.json()["detail"]["code"] == "external_write_uncertain"
        detail = client.get(f"/api/recordings/{recording_id}").json()
        assert detail["status"] == "ready_for_review"
        assert detail["failure"]["code"] == "external_write_uncertain"
        assert detail["groups"][0]["candidates"][0]["decision"] == "approved"
