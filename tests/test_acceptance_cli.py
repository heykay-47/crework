from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import main as cli
import feedback_triage.analyze as analysis_module
from feedback_triage.acceptance import AcceptanceStore
from feedback_triage.evaluation import load_ground_truth
from feedback_triage.github import GitHubIssue, GitHubIssueClient
from feedback_triage.input_video import VideoInfo
from feedback_triage.ledger import RunLedger
from feedback_triage.models import (
    AnalysisResult,
    analysis_to_wire,
    EvidenceSpan,
    EvidenceFrameRecord,
    Observation,
    PolicyResult,
    VerifiedAnalysis,
)
from feedback_triage.writes import ReviewDecision
from tests.test_completion_trust import VALID_ANALYSIS_JSON
from tests.test_write_coordinator import candidate


CANONICAL_SOURCE = cli.CANONICAL_SOURCE_SHA256
GROUND_TRUTH = load_ground_truth(cli.CANONICAL_GROUND_TRUTH)
GROUND_TRUTH_SHA = "d" * 64


def _baseline(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "source_sha256": CANONICAL_SOURCE,
                "recording_label": "previously-unseen-canonical-recording",
                "equivalent_issue_count": 3,
                "equivalent_issue_numbers": [101, 102, 103],
                "watch_seconds": 360.0,
                "issue_writing_seconds": 120.0,
                "active_human_seconds": 480.0,
                "measured_at": "2026-09-18T00:00:00+00:00",
            }
        )
    )


def _args(tmp_path: Path) -> Any:
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)
    return cli.parser().parse_args(
        [
            "run",
            str(tmp_path / "recording.mp4"),
            "--output",
            str(tmp_path / "output"),
            "--reanalyze",
            "--repository",
            "demo/feedback",
            "--manual-baseline",
            str(baseline),
        ]
    )


def _seed_acceptance(args: Any) -> AcceptanceStore:
    store = cli._acceptance_store(
        args,
        source_sha256=CANONICAL_SOURCE,
        prompt="prompt",
        project_context="context",
        fixture_version=GROUND_TRUTH.fixture_version,
        ground_truth_sha256=GROUND_TRUTH_SHA,
    )
    analysis = AnalysisResult.model_validate_json(VALID_ANALYSIS_JSON)
    policy = _canonical_policy()
    ledger = RunLedger.create(
        args.output,
        source_sha256=CANONICAL_SOURCE,
        fingerprint=store.fingerprint,
        fingerprint_inputs={"source": CANONICAL_SOURCE},
    )
    store.begin("analyze", reanalyze=False)
    for reanalyze in (False, True):
        if reanalyze:
            store.begin("analyze", reanalyze=True)
        attempt_id = ledger.start_attempt()
        ledger.record_interaction_created(attempt_id, f"seed-interaction-{len(ledger.attempts) + 1}")
        ledger.complete(attempt_id, analysis.model_dump(mode="json"), processing_pair_count=1)
        for name, seconds in {
            "upload_seconds": 1.0,
            "analysis_seconds": 2.0,
            "wall_clock_seconds": 3.0,
            "semantic_score_passed": 1.0,
        }.items():
            ledger.record_measurement(attempt_id, name, seconds)
        ledger.record_gemini_usage(attempt_id, {"total_token_count": 18})
        ledger.record_policy_result(
            attempt_id,
            policy.model_dump(mode="json"),
        )
        store.finish(attempt_id, qualified=True)
    return store


def _patch_fixture_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "_load_analysis_inputs",
        lambda args: (GROUND_TRUTH, "prompt", "context", GROUND_TRUTH_SHA),
    )
    monkeypatch.setattr(cli, "file_sha256", lambda path: CANONICAL_SOURCE)


def _canonical_policy() -> PolicyResult:
    values = [
        candidate("cand_2c8c489a2f60f030").model_copy(
            update={
                "topic_key": "hero-cta-label",
                "acceptance_criteria": (),
                "evidence": (EvidenceSpan(start_seconds=20.0, end_seconds=21.0, client_quote="button Schedule a Call"),),
                "evidence_frame_seconds": 20.5,
            }
        ),
        candidate("cand_481753781d347a60").model_copy(
            update={
                "topic_key": "mobile-navbar-logo-overlap",
                "type": "bug",
                "intent": "explicit_problem",
                "requested_outcome": "Fix the logo and menu overlap",
                "acceptance_criteria": (),
                "evidence": (
                    EvidenceSpan(start_seconds=80.0, end_seconds=81.0, client_quote="menu logo", visual_observation="logo overlaps"),
                    EvidenceSpan(start_seconds=220.0, end_seconds=221.0, client_quote="menu logo", visual_observation="logo overlaps"),
                ),
                "evidence_frame_seconds": 80.5,
            }
        ),
        candidate("cand_f246c26704fabaae").model_copy(
            update={
                "route": "clarification_request",
                "approval_eligible": False,
                "topic_key": "pricing-section-reaction",
                "type": "reaction",
                "intent": "ambiguous_reaction",
                "candidate_id": "cand_f246c26704fabaae",
                "requested_outcome": None,
                "acceptance_criteria": (),
                "clarification_question": "What should change in pricing?",
                "evidence": (EvidenceSpan(start_seconds=160.0, end_seconds=161.0, client_quote="pricing section"),),
                "evidence_frame_seconds": 160.5,
            }
        ),
        candidate("cand_9752fd020e6a3390").model_copy(
            update={
                "route": "withheld_result",
                "approval_eligible": False,
                "topic_key": "hero-cta-analytics",
                "type": "question",
                "intent": "question",
                "reason_code": "question_not_request",
                "candidate_id": "cand_9752fd020e6a3390",
                "requested_outcome": None,
                "acceptance_criteria": (),
                "clarification_question": None,
                "evidence": (EvidenceSpan(start_seconds=270.0, end_seconds=271.0, client_quote="analytics button"),),
                "evidence_frame_seconds": 270.5,
            }
        ),
        candidate("cand_191f3b4d6d46effb").model_copy(
            update={
                "route": "manual_review",
                "approval_eligible": False,
                "topic_key": "contact-form-submit-anomaly",
                "type": "bug",
                "intent": "none",
                "candidate_id": "cand_191f3b4d6d46effb",
                "requested_outcome": None,
                "acceptance_criteria": (),
                "clarification_question": None,
                "visually_inferred": True,
                "evidence": (EvidenceSpan(start_seconds=320.0, end_seconds=321.0, visual_observation="submit button anomaly"),),
                "evidence_frame_seconds": 320.5,
            }
        ),
    ]
    return PolicyResult(schema_version="1.0", results=values)


def _canonical_analysis() -> AnalysisResult:
    return AnalysisResult(
        schema_version="1.0",
        video_summary="Canonical six-case feedback recording.",
        observations=[
            Observation(
                observation_id="obs_001",
                topic_key="hero-cta-label",
                type="change_request",
                intent="explicit_change",
                title="Change the hero CTA label",
                component="hero",
                summary="The button label should change.",
                requested_outcome="Use Schedule a Call",
                acceptance_criteria=[],
                clarification_question=None,
                confidence="high",
                evidence=[
                    EvidenceSpan(
                        start_seconds=20.0,
                        end_seconds=21.0,
                        client_quote="button Schedule a Call",
                    )
                ],
                rationale="Explicit client request.",
            ),
            Observation(
                observation_id="obs_002",
                topic_key="mobile-navbar-logo-overlap",
                type="bug",
                intent="explicit_problem",
                title="Fix the mobile navbar overlap",
                component="navbar",
                summary="The logo overlaps the menu.",
                requested_outcome="Fix the logo and menu overlap",
                acceptance_criteria=[],
                clarification_question=None,
                confidence="high",
                evidence=[
                    EvidenceSpan(
                        start_seconds=80.0,
                        end_seconds=81.0,
                        client_quote="menu logo",
                        visual_observation="logo overlaps",
                    )
                ],
                rationale="Explicit client-reported bug.",
            ),
            Observation(
                observation_id="obs_003",
                topic_key="mobile-navbar-logo-overlap",
                type="bug",
                intent="explicit_problem",
                title="Fix the mobile navbar overlap",
                component="navbar",
                summary="The logo overlaps the menu.",
                requested_outcome="Fix the logo and menu overlap",
                acceptance_criteria=[],
                clarification_question=None,
                confidence="high",
                evidence=[
                    EvidenceSpan(
                        start_seconds=220.0,
                        end_seconds=221.0,
                        client_quote="menu logo",
                        visual_observation="logo overlaps",
                    )
                ],
                rationale="Repeated explicit client-reported bug.",
            ),
            Observation(
                observation_id="obs_004",
                topic_key="pricing-section-reaction",
                type="reaction",
                intent="ambiguous_reaction",
                title="Clarify the pricing reaction",
                component="pricing",
                summary="The client reacted to the pricing section without a clear request.",
                requested_outcome=None,
                acceptance_criteria=[],
                clarification_question="What should change in pricing?",
                confidence="high",
                evidence=[
                    EvidenceSpan(start_seconds=160.0, end_seconds=161.0, client_quote="pricing section")
                ],
                rationale="The requested change is ambiguous.",
            ),
            Observation(
                observation_id="obs_005",
                topic_key="hero-cta-analytics",
                type="question",
                intent="question",
                title="Analytics question",
                component="hero",
                summary="The client asked a question about analytics.",
                requested_outcome=None,
                acceptance_criteria=[],
                clarification_question=None,
                confidence="high",
                evidence=[
                    EvidenceSpan(start_seconds=270.0, end_seconds=271.0, client_quote="analytics button")
                ],
                rationale="A question is not a requested implementation change.",
            ),
            Observation(
                observation_id="obs_006",
                topic_key="contact-form-submit-anomaly",
                type="bug",
                intent="none",
                title="Review the contact form submit anomaly",
                component="contact-form",
                summary="The submit button appears anomalous.",
                requested_outcome=None,
                acceptance_criteria=[],
                clarification_question=None,
                confidence="high",
                evidence=[
                    EvidenceSpan(
                        start_seconds=320.0,
                        end_seconds=321.0,
                        visual_observation="submit button anomaly",
                    )
                ],
                rationale="This is a visual-only observation requiring human review.",
            ),
        ],
    )


class _GeminiFiles:
    def upload(self, *, file: str) -> Any:
        return SimpleNamespace(
            name="files/canonical",
            uri="gemini://canonical",
            mime_type="video/mp4",
            state=SimpleNamespace(name="ACTIVE"),
        )

    def get(self, *, name: str) -> Any:
        return SimpleNamespace(state=SimpleNamespace(name="ACTIVE"), name=name)


class _GeminiInteractions:
    def __init__(self, analysis: AnalysisResult) -> None:
        self.analysis = analysis
        self.create_ids: list[str] = []
        self.get_ids: list[str] = []

    def create(self, **kwargs: Any) -> Iterator[Any]:
        interaction_id = f"interaction-{len(self.create_ids) + 1}"
        self.create_ids.append(interaction_id)
        yield SimpleNamespace(event_type="interaction.created", interaction=SimpleNamespace(id=interaction_id))
        yield SimpleNamespace(event_type="interaction.completed")

    def get(self, *, id: str) -> Any:
        self.get_ids.append(id)
        return SimpleNamespace(
            status="completed",
            output_text=analysis_to_wire(self.analysis).model_dump_json(),
            steps=[
                SimpleNamespace(type="processing_call", id=f"{id}-processing"),
                SimpleNamespace(type="processing_result", call_id=f"{id}-processing"),
            ],
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=50,
                total_token_count=150,
            ),
        )


class _GeminiGateway:
    def __init__(self, analysis: AnalysisResult) -> None:
        self.files = _GeminiFiles()
        self.interactions = _GeminiInteractions(analysis)


def test_run_preflight_is_read_only_and_precedes_the_third_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    _seed_acceptance(args)
    _patch_fixture_inputs(monkeypatch)
    triage_called = False

    class ExistingMarkerGateway:
        def __init__(self, token: str) -> None:
            pass

        def find_marker(self, repository: str, marker: str) -> tuple[GitHubIssue, ...]:
            nonlocal triage_called
            triage_called = True
            return (GitHubIssue(repository, 99, "old", marker, "https://example.test/99", "closed"),)

        def get_issue(self, repository: str, number: int) -> GitHubIssue:
            return GitHubIssue(repository, number, "old", "", f"https://example.test/{number}", "closed")

        def create_issue(self, repository: str, payload: object) -> GitHubIssue:
            raise AssertionError("preflight must not POST")

    monkeypatch.setattr(cli, "GitHubIssueClient", ExistingMarkerGateway)
    monkeypatch.setattr(cli, "triage_recording", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("analysis ran")))

    assert cli.run_main(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "acceptance_preflight"
    assert triage_called is True
    reloaded = AcceptanceStore(
        args.output / CANONICAL_SOURCE / "acceptance.json",
        source_sha256=CANONICAL_SOURCE,
        fingerprint=cli._acceptance_store(
            args,
            source_sha256=CANONICAL_SOURCE,
            prompt="prompt",
            project_context="context",
            fixture_version=GROUND_TRUTH.fixture_version,
            ground_truth_sha256=GROUND_TRUTH_SHA,
        ).fingerprint,
    )
    assert reloaded.progress.next_step == "analyze"


def test_run_rejects_acceptance_ids_without_verified_ledger_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    store = cli._acceptance_store(
        args,
        source_sha256=CANONICAL_SOURCE,
        prompt="prompt",
        project_context="context",
        fixture_version=GROUND_TRUTH.fixture_version,
        ground_truth_sha256=GROUND_TRUTH_SHA,
    )
    store.begin("analyze", reanalyze=False)
    store.finish("missing-attempt-1", qualified=True)
    store.begin("analyze", reanalyze=True)
    store.finish("missing-attempt-2", qualified=True)
    _patch_fixture_inputs(monkeypatch)
    monkeypatch.setattr(cli, "GitHubIssueClient", lambda token: (_ for _ in ()).throw(AssertionError("preflight ran")))
    monkeypatch.setattr(cli, "triage_recording", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("analysis ran")))

    assert cli.run_main(args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "acceptance_history"


def test_run_rejects_an_interleaved_ledger_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    _seed_acceptance(args)
    ledger = RunLedger.load(args.output / CANONICAL_SOURCE / "ledger.json")
    analysis = AnalysisResult.model_validate_json(VALID_ANALYSIS_JSON)
    interleaved = ledger.start_attempt()
    ledger.record_interaction_created(interleaved, "interleaved-interaction")
    ledger.complete(interleaved, analysis.model_dump(mode="json"), processing_pair_count=1)
    for name, seconds in {
        "upload_seconds": 1.0,
        "analysis_seconds": 2.0,
        "wall_clock_seconds": 3.0,
        "semantic_score_passed": 1.0,
    }.items():
        ledger.record_measurement(interleaved, name, seconds)
    ledger.record_gemini_usage(interleaved, {"total_token_count": 18})
    ledger.record_policy_result(interleaved, _canonical_policy().model_dump(mode="json"))
    _patch_fixture_inputs(monkeypatch)
    monkeypatch.setattr(cli, "GitHubIssueClient", lambda token: FakeIssueGateway())
    monkeypatch.setattr(cli, "triage_recording", lambda *values, **kwargs: _fake_triage(args.output, frames_extracted=True))

    assert cli.run_main(args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "acceptance_history"


def _fake_triage(
    output: Path,
    *,
    frames_extracted: bool,
    fingerprint: str | None = None,
) -> tuple[VideoInfo, VerifiedAnalysis, PolicyResult, RunLedger]:
    analysis = AnalysisResult.model_validate_json(VALID_ANALYSIS_JSON)
    policy = _canonical_policy()
    ledger_path = output / CANONICAL_SOURCE / "ledger.json"
    ledger = RunLedger.load(ledger_path) if ledger_path.exists() else RunLedger.create(
        output,
        source_sha256=CANONICAL_SOURCE,
        fingerprint=fingerprint or "e" * 64,
        fingerprint_inputs={"source": CANONICAL_SOURCE},
    )
    attempt_id = ledger.start_attempt()
    ledger.record_interaction_created(attempt_id, f"fake-interaction-{len(ledger.attempts)}")
    ledger.complete(attempt_id, analysis.model_dump(mode="json"), processing_pair_count=1)
    for name, seconds in {
        "upload_seconds": 1.0,
        "analysis_seconds": 2.0,
        "frame_seconds": 3.0,
    }.items():
        ledger.record_measurement(attempt_id, name, seconds)
    ledger.record_gemini_usage(attempt_id, {"total_token_count": 18})
    ledger.record_policy_result(attempt_id, policy.model_dump(mode="json"))
    for value in policy.results:
        ledger.record_evidence_frame(
            attempt_id,
            EvidenceFrameRecord(
                candidate_id=value.candidate_id,
                timestamp_seconds=1.0,
                status="extracted" if frames_extracted else "failed",
                path=f"frame-{value.candidate_id}.png" if frames_extracted else None,
                error=None if frames_extracted else "ffmpeg failed",
            ),
        )
    return (
        VideoInfo(path=Path("recording.mp4"), duration_seconds=360.026667),
        VerifiedAnalysis(analysis=analysis, processing_pair_count=1, gemini_usage={"total_token_count": 18}),
        policy,
        ledger,
    )


def test_acceptance_cli_runs_the_exact_three_step_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_args = _args(tmp_path)
    analyze_args = cli.parser().parse_args(
        ["analyze", str(tmp_path / "recording.mp4"), "--output", str(tmp_path / "output")]
    )
    _patch_fixture_inputs(monkeypatch)
    gateway = FakeIssueGateway()
    gemini = _GeminiGateway(_canonical_analysis())
    extracted_candidates: list[str] = []

    def extract_frame(
        video: Path, *, candidate_id: str, timestamp_seconds: float, output_path: Path
    ) -> EvidenceFrameRecord:
        extracted_candidates.append(candidate_id)
        return EvidenceFrameRecord(
            candidate_id=candidate_id,
            timestamp_seconds=timestamp_seconds,
            status="extracted",
            path=str(output_path),
        )

    monkeypatch.setattr(cli, "GitHubIssueClient", lambda token: gateway)
    monkeypatch.setattr(analysis_module, "create_client", lambda: gemini)
    monkeypatch.setattr(
        analysis_module,
        "probe_video",
        lambda path: VideoInfo(path=path, duration_seconds=GROUND_TRUTH.duration_seconds),
    )
    monkeypatch.setattr(analysis_module, "file_sha256", lambda path: CANONICAL_SOURCE)
    monkeypatch.setattr(analysis_module, "extract_evidence_frame", extract_frame)
    monkeypatch.setattr(
        cli,
        "terminal_review_decision",
        lambda candidate_result, **kwargs: ReviewDecision(
            "approve", manual_review_confirmed=candidate_result.route == "manual_review"
        ),
    )

    assert cli.analyze_main(analyze_args) == 0
    capsys.readouterr()
    analyze_args.reanalyze = True
    assert cli.analyze_main(analyze_args) == 0
    capsys.readouterr()
    assert cli.run_main(run_args) == 0
    output = capsys.readouterr().out

    assert len(gemini.interactions.create_ids) == 3
    assert gemini.interactions.get_ids == gemini.interactions.create_ids
    assert extracted_candidates == [
        "cand_2c8c489a2f60f030",
        "cand_481753781d347a60",
        "cand_191f3b4d6d46effb",
    ]
    assert '"status": "acceptance_passed"' in output
    assert gateway.created_ids == [
        "cand_2c8c489a2f60f030",
        "cand_481753781d347a60",
        "cand_191f3b4d6d46effb",
    ]


def test_run_rejects_failed_evidence_frames_before_review_or_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    _seed_acceptance(args)
    _patch_fixture_inputs(monkeypatch)
    monkeypatch.setattr(cli, "GitHubIssueClient", lambda token: FakeIssueGateway())
    monkeypatch.setattr(cli, "triage_recording", lambda *args, **kwargs: _fake_triage(tmp_path / "output", frames_extracted=False))
    monkeypatch.setattr(cli, "WriteCoordinator", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("review ran")))

    assert cli.run_main(args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "acceptance_frames"


def test_run_creates_exactly_three_issues_after_visual_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    _seed_acceptance(args)
    _patch_fixture_inputs(monkeypatch)
    gateway = FakeIssueGateway()
    monkeypatch.setattr(cli, "GitHubIssueClient", lambda token: gateway)
    monkeypatch.setattr(cli, "triage_recording", lambda *args, **kwargs: _fake_triage(tmp_path / "output", frames_extracted=True))
    reviewed_ids: list[str] = []

    def approve(candidate_result: Any, **kwargs: Any) -> ReviewDecision:
        reviewed_ids.append(candidate_result.candidate_id)
        return ReviewDecision("approve", manual_review_confirmed=candidate_result.route == "manual_review")

    monkeypatch.setattr(cli, "terminal_review_decision", approve)

    assert cli.run_main(args) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count('"state": "created"') == 3
    assert reviewed_ids == [
        "cand_2c8c489a2f60f030",
        "cand_481753781d347a60",
        "cand_191f3b4d6d46effb",
    ]
    assert gateway.created_ids == reviewed_ids


class FakeIssueGateway:
    def __init__(self) -> None:
        self.create_calls = 0
        self.created_ids: list[str] = []

    def find_marker(self, repository: str, marker: str) -> tuple[GitHubIssue, ...]:
        return ()

    def get_issue(self, repository: str, number: int) -> GitHubIssue:
        return GitHubIssue(repository, number, "old", "", f"https://example.test/{number}", "closed")

    def create_issue(self, repository: str, payload: Any) -> GitHubIssue:
        self.create_calls += 1
        self.created_ids.append(payload.body.split("candidate_id=", 1)[1].split(" ", 1)[0])
        return GitHubIssue(
            repository,
            self.create_calls,
            payload.title,
            payload.body,
            f"https://example.test/{self.create_calls}",
            "open",
        )
