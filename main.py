import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Sequence

from feedback_triage.acceptance import AcceptanceError, AcceptanceStore, ManualBaseline
from feedback_triage.analyze import AnalysisFailed, analysis_fingerprint, file_sha256, triage_recording
from feedback_triage.github import GitHubApiError, GitHubIssueClient
from feedback_triage.evaluation import GroundTruthManifest, ScoreReport, load_ground_truth, score_policy_result
from feedback_triage.gemini_video import PROMPT
from feedback_triage.ledger import LedgerInvalid, LedgerLocked, RunLedger
from feedback_triage.models import PolicyResult, VerifiedAnalysis
from feedback_triage.writes import ExternalWriteFailure, WriteCoordinator, terminal_review_decision


CANONICAL_GROUND_TRUTH = Path(__file__).resolve().parent / "fixtures" / "canonical" / "ground-truth.json"
CANONICAL_FIXTURE_VERSION = "canonical-six-case-v3"
CANONICAL_SOURCE_SHA256 = "f0b72e2f45e33166616e18293b1326be5fd8d1d8635a4e6a3770b516e9773fdc"


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="feedback-triage")
    subcommands = command_parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="analyze one owned synthetic Feedback Recording")
    analyze.add_argument("video", type=Path)
    analyze.add_argument("--output", type=Path, default=Path("output"))
    analyze.add_argument("--reanalyze", action="store_true", help="append a replacement attempt after reconciliation")
    analyze.add_argument("--prompt", type=Path, help="frozen prompt text to send with the recording")
    analyze.add_argument("--context", type=Path, help="project context text to send with the recording")
    analyze.add_argument("--ground-truth", type=Path, help="frozen JSON manifest used for semantic scoring")
    run = subcommands.add_parser(
        "run",
        help="execute the third, reviewed and write-enabled step of the frozen acceptance cycle",
    )
    run.add_argument("video", type=Path)
    run.add_argument("--output", type=Path, default=Path("output"))
    run.add_argument("--reanalyze", action="store_true", required=True)
    run.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    run.add_argument("--operator-label")
    run.add_argument("--prompt", type=Path)
    run.add_argument("--context", type=Path)
    run.add_argument(
        "--ground-truth",
        type=Path,
        default=Path("fixtures/canonical/ground-truth.json"),
        help="frozen JSON manifest used for semantic scoring",
    )
    run.add_argument(
        "--manual-baseline",
        type=Path,
        default=(Path(os.environ["CREWORK_MANUAL_BASELINE"]) if os.environ.get("CREWORK_MANUAL_BASELINE") else None),
        help="measured baseline JSON for the same recording",
    )
    publish = subcommands.add_parser(
        "publish",
        help="review admitted Candidates and write explicitly approved GitHub Issues",
    )
    publish.add_argument("video", type=Path)
    publish.add_argument("--output", type=Path, default=Path("output"))
    publish.add_argument("--repository", required=True, help="destination GitHub repository owner/name")
    publish.add_argument("--operator-label", help="optional audit label recorded with each Approval")
    publish.add_argument("--reconsider", action="store_true", help="explicitly revisit unchanged declined snapshots")
    publish.add_argument("--retry-failed", action="store_true", help="explicitly retry a definitive create failure")
    publish.add_argument(
        "--retry-uncertain",
        action="store_true",
        help="request retry of an uncertain write after certifying no Issue was found",
    )
    publish.add_argument(
        "--confirm-no-issue",
        action="store_true",
        help="record the human certification required with --retry-uncertain",
    )
    publish.add_argument(
        "--canonical-selection",
        action="append",
        default=[],
        metavar="CANDIDATE_ID=ISSUE_NUMBER",
        help="explicitly select the canonical Issue for a recorded marker conflict; repeat per Candidate",
    )
    return command_parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "publish":
        return publish_main(args)
    if args.command == "run":
        return run_main(args)
    return analyze_main(args)


def _load_analysis_inputs(args: argparse.Namespace) -> tuple[GroundTruthManifest | None, str, str, str | None]:
    try:
        ground_truth_path = args.ground_truth
        if ground_truth_path is None and args.command == "analyze":
            canonical_path = CANONICAL_GROUND_TRUTH
            if canonical_path.exists():
                canonical = load_ground_truth(canonical_path)
                if canonical.source_sha256 == file_sha256(args.video):
                    ground_truth_path = canonical_path
        ground_truth = load_ground_truth(ground_truth_path) if ground_truth_path else None
        fixture_dir = ground_truth_path.parent if ground_truth_path else None
        prompt_path = args.prompt or (fixture_dir / "prompt.md" if fixture_dir else None)
        context_path = args.context or (fixture_dir / "project-context.md" if fixture_dir else None)
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path else None
        project_context = context_path.read_text(encoding="utf-8") if context_path else ""
        ground_truth_sha256 = file_sha256(ground_truth_path) if ground_truth_path else None
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid_fixture: {error}") from error
    return ground_truth, prompt if prompt is not None else PROMPT, project_context, ground_truth_sha256


def _acceptance_store(
    args: argparse.Namespace,
    *,
    source_sha256: str,
    prompt: str,
    project_context: str,
    fixture_version: str | None,
    ground_truth_sha256: str | None,
) -> AcceptanceStore:
    fingerprint = analysis_fingerprint(
        source_sha256,
        prompt=prompt,
        project_context=project_context,
        fixture_version=fixture_version,
        ground_truth_sha256=ground_truth_sha256,
    )
    return AcceptanceStore(
        args.output / source_sha256 / "acceptance.json",
        source_sha256=source_sha256,
        fingerprint=fingerprint,
    )


def _latest_attempt_id(ledger: RunLedger) -> str:
    attempts = ledger.attempts
    if not attempts:
        raise LedgerInvalid("analysis produced no Run Ledger attempt")
    return str(attempts[-1]["attempt_id"])


def _record_measurements(ledger: RunLedger | None, attempt_id: str, measurements: dict[str, float]) -> None:
    """Persist measurements under the same source lock used by write transitions."""

    if ledger is None or not measurements or not attempt_id or attempt_id == "unknown":
        return
    with ledger.exclusive_lock():
        for name, seconds in measurements.items():
            ledger.record_measurement(attempt_id, name, seconds)


def _best_effort_record_measurements(
    ledger: RunLedger | None, attempt_id: str, measurements: dict[str, float]
) -> None:
    try:
        _record_measurements(ledger, attempt_id, measurements)
    except (LedgerInvalid, LedgerLocked, OSError, ValueError):
        pass


def _source_marker_prefix(source_sha256: str) -> str:
    return f"<!-- crework:v1 source_sha256={source_sha256} candidate_id="


def _require_no_source_markers(github: GitHubIssueClient, repository: str, source_sha256: str) -> None:
    if github.find_marker(repository, _source_marker_prefix(source_sha256)):
        raise AcceptanceError(
            "acceptance_preflight",
            "an exact final-source marker already exists; no cleanup is performed",
        )


def _require_closed_demo_issues(
    github: GitHubIssueClient, repository: str, issue_numbers: tuple[int, ...]
) -> None:
    for number in issue_numbers:
        issue = github.get_issue(repository, number)
        if issue.state != "closed":
            raise AcceptanceError(
                "acceptance_preflight",
                f"manual-baseline Issue #{number} is not closed; no cleanup is performed",
            )


def _attempt_has_analysis_proof(attempt: dict[str, object], *, full_run: bool) -> bool:
    measurements = attempt.get("measurements")
    usage = attempt.get("gemini_usage")
    processing_pair_count = attempt.get("processing_pair_count")
    interaction_id = attempt.get("interaction_id")
    if (
        not isinstance(measurements, dict)
        or not isinstance(usage, dict)
        or not usage
        or not isinstance(attempt.get("analysis"), dict)
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
    if full_run:
        required.update(
            {
                "frame_seconds",
                "active_review_seconds",
                "active_human_seconds",
                "write_seconds",
                "external_write_count",
            }
        )
    if not required <= set(measurements):
        return False
    for name in required:
        value = measurements.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return False
    return measurements.get("semantic_score_passed") == 1.0


def _require_backed_acceptance_history(
    acceptance: AcceptanceStore,
    attempts: tuple[dict[str, object], ...],
    *,
    ground_truth: GroundTruthManifest,
    source_sha256: str,
    duration_seconds: float,
) -> None:
    qualified_ids = acceptance.progress.qualified_attempt_ids
    if len(qualified_ids) != 2:
        raise AcceptanceError(
            "acceptance_history",
            "the third acceptance step requires exactly two prior qualifying attempts",
        )
    if len(set(qualified_ids)) != 2:
        raise AcceptanceError(
            "acceptance_history",
            "the third acceptance step requires two distinct prior qualifying attempts",
        )
    history_ids = [
        str(attempt.get("attempt_id"))
        for attempt in attempts
        if isinstance(attempt.get("attempt_id"), str)
    ]
    try:
        first_index = history_ids.index(qualified_ids[0])
        second_index = history_ids.index(qualified_ids[1])
    except ValueError as error:
        raise AcceptanceError(
            "acceptance_history",
            "qualifying acceptance attempts are not present in ledger order",
        ) from error
    if second_index != first_index + 1 or second_index != len(history_ids) - 2:
        raise AcceptanceError(
            "acceptance_history",
            "the third acceptance step requires two adjacent prior ledger attempts",
        )
    by_id = {
        str(attempt.get("attempt_id")): attempt
        for attempt in attempts
        if isinstance(attempt.get("attempt_id"), str)
    }
    for attempt_id in qualified_ids:
        attempt = by_id.get(attempt_id)
        if (
            attempt is None
            or attempt.get("status") != "verified"
            or not isinstance(attempt.get("policy_result"), dict)
            or not _attempt_has_analysis_proof(attempt, full_run=False)
        ):
            raise AcceptanceError(
                "acceptance_history",
                f"qualifying acceptance attempt {attempt_id} is not backed by a verified Run Ledger attempt",
            )
        try:
            prior_policy = PolicyResult.model_validate(attempt["policy_result"])
            prior_score = score_policy_result(
                prior_policy,
                ground_truth,
                source_sha256=source_sha256,
                duration_seconds=duration_seconds,
            )
        except (TypeError, ValueError) as error:
            raise AcceptanceError(
                "acceptance_history",
                f"qualifying acceptance attempt {attempt_id} has invalid persisted policy proof",
            ) from error
        if not prior_score.passed:
            raise AcceptanceError(
                "acceptance_history",
                f"qualifying acceptance attempt {attempt_id} does not have a passing canonical semantic score",
            )


def _require_current_acceptance_ledger(acceptance: AcceptanceStore, ledger: RunLedger | None) -> RunLedger:
    if ledger is None or ledger.analysis_fingerprint != acceptance.fingerprint:
        raise AcceptanceError(
            "acceptance_history",
            "qualifying acceptance attempts must belong to the current behavior fingerprint",
        )
    return ledger


def _require_canonical_fixture(args: argparse.Namespace, ground_truth: GroundTruthManifest) -> None:
    if args.ground_truth is None or args.ground_truth.resolve() != CANONICAL_GROUND_TRUTH:
        raise AcceptanceError("acceptance_fixture", "the frozen acceptance cycle requires the canonical ground-truth fixture")
    if ground_truth.fixture_version != CANONICAL_FIXTURE_VERSION or ground_truth.source_sha256 != CANONICAL_SOURCE_SHA256:
        raise AcceptanceError("acceptance_fixture", "the acceptance fixture identity does not match the frozen canonical fixture")
    if args.prompt is not None or args.context is not None:
        raise AcceptanceError("acceptance_fixture", "the frozen acceptance cycle does not permit prompt or context overrides")


def _score(
    policy: PolicyResult,
    ground_truth: GroundTruthManifest | None,
    *,
    source_sha256: str,
    duration_seconds: float,
) -> ScoreReport | None:
    return (
        score_policy_result(
            policy,
            ground_truth,
            source_sha256=source_sha256,
            duration_seconds=duration_seconds,
        )
        if ground_truth is not None
        else None
    )


def _analysis_output(
    *,
    status: str,
    ledger: RunLedger,
    verified: VerifiedAnalysis,
    policy: PolicyResult,
    score: ScoreReport | None,
    acceptance: AcceptanceStore | None,
    include_frames: bool,
) -> None:
    attempts = ledger.attempts
    evidence_frames = ledger.evidence_frames_for_attempt(_latest_attempt_id(ledger)) if include_frames and attempts else ()
    print(
        json.dumps(
            {
                "status": status,
                "ledger": str(ledger.path),
                "processing_pair_count": verified.processing_pair_count,
                "gemini_usage": verified.gemini_usage,
                "analysis": verified.analysis.model_dump(mode="json"),
                "policy_result": policy.model_dump(mode="json"),
                "evidence_frames": [frame.model_dump(mode="json") for frame in evidence_frames],
                "score": score.model_dump(mode="json") if score is not None else None,
                "acceptance": acceptance.record.model_dump(mode="json") if acceptance is not None else None,
            },
            indent=2,
        )
    )


def analyze_main(args: argparse.Namespace) -> int:
    try:
        ground_truth, prompt, project_context, ground_truth_sha256 = _load_analysis_inputs(args)
    except ValueError as error:
        print(json.dumps({"status": "failed", "code": "invalid_fixture", "detail": str(error)}))
        return 1
    acceptance: AcceptanceStore | None = None
    started = time.monotonic()
    before_attempt_ids: set[str] = set()
    try:
        if ground_truth is not None:
            source_sha256 = file_sha256(args.video)
            existing_ledger_path = args.output / source_sha256 / "ledger.json"
            if existing_ledger_path.exists():
                existing_ledger = RunLedger.load(existing_ledger_path)
                before_attempt_ids = {str(attempt["attempt_id"]) for attempt in existing_ledger.attempts}
            acceptance = _acceptance_store(
                args,
                source_sha256=source_sha256,
                prompt=prompt,
                project_context=project_context,
                fixture_version=ground_truth.fixture_version,
                ground_truth_sha256=ground_truth_sha256,
            )
            acceptance.begin("analyze", reanalyze=args.reanalyze)
        analysis_reanalyze = args.reanalyze or (
            acceptance is not None and acceptance.progress.next_step == "analyze"
        )
        video_info, verified, policy, ledger = triage_recording(
            args.video,
            args.output,
            reanalyze=analysis_reanalyze,
            prompt=prompt,
            project_context=project_context,
            fixture_version=ground_truth.fixture_version if ground_truth is not None else None,
            ground_truth_sha256=ground_truth_sha256,
            extract_evidence_frames=ground_truth is None,
            reset_on_fingerprint_mismatch=ground_truth is not None,
        )
    except AcceptanceError as error:
        print(json.dumps({"status": "failed", "code": error.code, "detail": str(error)}))
        return 1
    except AnalysisFailed as error:
        if acceptance is not None:
            acceptance.finish("unknown", qualified=False, reason=error.code)
        print(json.dumps({"status": "failed", "code": error.code, "detail": str(error)}))
        return 1
    except LedgerInvalid as error:
        if acceptance is not None:
            acceptance.finish("unknown", qualified=False, reason="ledger_invalid")
        print(json.dumps({"status": "failed", "code": "ledger_invalid", "detail": str(error)}))
        return 1
    except (LedgerLocked, OSError, ValueError) as error:
        if acceptance is not None:
            acceptance.finish("unknown", qualified=False, reason="analysis_failed")
        print(json.dumps({"status": "failed", "code": "analysis_failed", "detail": str(error)}))
        return 1
    attempt_id = _latest_attempt_id(ledger) if ledger.attempts else "unknown"
    score = _score(
        policy,
        ground_truth,
        source_sha256=ledger.source_sha256,
        duration_seconds=video_info.duration_seconds,
    )
    try:
        _record_measurements(
            ledger,
            attempt_id,
            {
                "wall_clock_seconds": time.monotonic() - started,
                **({"semantic_score_passed": 1.0} if score is not None and score.passed else {}),
            },
        )
    except (LedgerInvalid, LedgerLocked, OSError, ValueError) as error:
        if acceptance is not None:
            acceptance.finish(attempt_id, qualified=False, reason="measurement_persist_failed")
            print(json.dumps({"status": "failed", "code": "measurement_persist_failed", "detail": str(error)}))
            return 1
        raise
    if acceptance is not None:
        latest_attempt = ledger.attempts[-1] if ledger.attempts else {}
        fresh = (
            attempt_id != "unknown"
            and attempt_id not in before_attempt_ids
            and latest_attempt.get("status") == "verified"
        )
        qualified = fresh and score is not None and score.passed and _attempt_has_analysis_proof(latest_attempt, full_run=False)
        acceptance.finish(
            attempt_id,
            qualified=qualified,
            reason=None
            if qualified
            else (
                "analysis_reused"
                if not fresh
                else "analysis_measurements_missing"
                if not _attempt_has_analysis_proof(latest_attempt, full_run=False)
                else "semantic_score_failed"
            ),
            metrics=latest_attempt.get("measurements", {}) if isinstance(latest_attempt.get("measurements"), dict) else {},
        )
    status = "ready_for_review"
    if score is not None and not score.passed:
        status = "semantic_score_failed"
    _analysis_output(
        status=status,
        ledger=ledger,
        verified=verified,
        policy=policy,
        score=score,
        acceptance=acceptance,
        include_frames=ground_truth is None,
    )
    return 0 if score is None or score.passed else 1


def run_main(args: argparse.Namespace) -> int:
    if not args.repository:
        print(json.dumps({"status": "failed", "code": "acceptance_invalid", "detail": "GITHUB_REPOSITORY or --repository is required"}))
        return 1
    try:
        ground_truth, prompt, project_context, ground_truth_sha256 = _load_analysis_inputs(args)
        if ground_truth is None:
            raise ValueError("run requires a ground-truth manifest")
        _require_canonical_fixture(args, ground_truth)
        source_sha256 = file_sha256(args.video)
        if source_sha256 != CANONICAL_SOURCE_SHA256:
            raise AcceptanceError("acceptance_fixture", "the acceptance recording does not match the frozen canonical source")
        acceptance = _acceptance_store(
            args,
            source_sha256=source_sha256,
            prompt=prompt,
            project_context=project_context,
            fixture_version=ground_truth.fixture_version,
            ground_truth_sha256=ground_truth_sha256,
        )
        if args.manual_baseline is not None:
            baseline = ManualBaseline.model_validate_json(args.manual_baseline.read_text(encoding="utf-8"))
            acceptance.set_manual_baseline(baseline)
        acceptance.begin("run", reanalyze=args.reanalyze)
        baseline = acceptance.require_manual_baseline(expected_issue_count=3)
    except (AcceptanceError, OSError, ValueError) as error:
        code = error.code if isinstance(error, AcceptanceError) else "acceptance_invalid"
        print(json.dumps({"status": "failed", "code": code, "detail": str(error)}))
        return 1

    started = time.monotonic()
    before_attempt_ids: set[str] = set()
    write_measurements: dict[str, float] = {}
    attempt_id = "unknown"
    ledger: RunLedger | None = None
    try:
        existing_ledger_path = args.output / source_sha256 / "ledger.json"
        existing_ledger: RunLedger | None = None
        if existing_ledger_path.exists():
            existing_ledger = RunLedger.load(existing_ledger_path)
            before_attempt_ids = {str(attempt["attempt_id"]) for attempt in existing_ledger.attempts}
        _require_current_acceptance_ledger(acceptance, existing_ledger)
        github = GitHubIssueClient(os.environ.get("GITHUB_TOKEN", ""))
        _require_closed_demo_issues(github, args.repository, baseline.equivalent_issue_numbers)
        _require_no_source_markers(github, args.repository, source_sha256)
        video_info, verified, policy, ledger = triage_recording(
            args.video,
            args.output,
            reanalyze=True,
            prompt=prompt,
            project_context=project_context,
            fixture_version=ground_truth.fixture_version,
            ground_truth_sha256=ground_truth_sha256,
            extract_evidence_frames=True,
            reset_on_fingerprint_mismatch=True,
        )
        attempt_id = _latest_attempt_id(ledger)
        if attempt_id in before_attempt_ids:
            raise AcceptanceError("analysis_not_fresh", "the third acceptance step reused an earlier analysis attempt")
        _require_backed_acceptance_history(
            acceptance,
            ledger.attempts,
            ground_truth=ground_truth,
            source_sha256=ledger.source_sha256,
            duration_seconds=video_info.duration_seconds,
        )
        score = _score(
            policy,
            ground_truth,
            source_sha256=ledger.source_sha256,
            duration_seconds=video_info.duration_seconds,
        )
        actionable = tuple(result for result in policy.results if result.route in {"candidate", "manual_review"})
        if score is None or not score.passed:
            raise AcceptanceError("semantic_score_failed", "the third analysis did not pass the frozen semantic score")
        write_measurements["semantic_score_passed"] = 1.0
        if len(actionable) != 3:
            raise AcceptanceError("acceptance_matrix", "the third analysis did not produce exactly three write routes")
        acceptance.require_manual_baseline(expected_issue_count=len(actionable))
        evidence_frames = {frame.candidate_id: frame for frame in ledger.evidence_frames_for_attempt(attempt_id)}
        for candidate in actionable:
            frame = evidence_frames.get(candidate.candidate_id)
            if frame is None or frame.status != "extracted" or frame.path is None:
                raise AcceptanceError(
                    "acceptance_frames",
                    "the third analysis did not extract an Evidence Frame for every actionable result",
                )
        _require_no_source_markers(github, args.repository, ledger.source_sha256)
        evidence_frame_paths = {
            frame.candidate_id: (frame.path,) if frame.path is not None else ()
            for frame in evidence_frames.values()
        }
        outcomes = WriteCoordinator(ledger, github).review_and_publish(
            policy.results,
            source_sha256=ledger.source_sha256,
            destination_repository=args.repository,
            attempt_id=attempt_id,
            decision_fn=lambda candidate: terminal_review_decision(
                candidate,
                emit=lambda text: print(text, file=sys.stderr),
                evidence_frame_paths=evidence_frame_paths.get(candidate.candidate_id, ()),
            ),
            operator_label=args.operator_label,
            reconsider=False,
            retry_failed=False,
            retry_uncertain=False,
            confirm_no_issue=False,
            canonical_issue_selections={},
            on_timing=write_measurements.__setitem__,
            require_fresh_review=True,
        )
        outcome_ids = {outcome.candidate_id for outcome in outcomes}
        if outcome_ids != {candidate.candidate_id for candidate in actionable} or any(
            outcome.state != "created" for outcome in outcomes
        ):
            raise AcceptanceError("acceptance_writes", "the third run did not create exactly three verified Issues")
        _record_measurements(
            ledger,
            attempt_id,
            {**write_measurements, "wall_clock_seconds": time.monotonic() - started},
        )
        latest_attempt = ledger.attempts[-1]
        if not _attempt_has_analysis_proof(latest_attempt, full_run=True):
            raise AcceptanceError(
                "acceptance_measurements",
                "the third run did not persist upload, analysis, frame, review, write, wall-clock, and Gemini-usage measurements",
            )
        acceptance.finish(
            attempt_id,
            qualified=True,
            metrics=latest_attempt.get("measurements", {})
            if isinstance(latest_attempt.get("measurements"), dict)
            else {},
        )
    except ExternalWriteFailure as error:
        _best_effort_record_measurements(ledger, attempt_id, write_measurements)
        acceptance.finish(attempt_id, qualified=False, reason=error.code)
        print(json.dumps({"status": "failed", "code": error.code, "detail": error.detail}))
        return 1
    except GitHubApiError as error:
        _best_effort_record_measurements(ledger, attempt_id, write_measurements)
        acceptance.finish(attempt_id, qualified=False, reason="github_preflight")
        print(json.dumps({"status": "failed", "code": "github_preflight", "detail": str(error)}))
        return 1
    except (AcceptanceError, AnalysisFailed, LedgerInvalid, LedgerLocked, OSError, ValueError) as error:
        _best_effort_record_measurements(ledger, attempt_id, write_measurements)
        code = error.code if isinstance(error, (AcceptanceError, AnalysisFailed)) else "acceptance_invalid"
        acceptance.finish(attempt_id, qualified=False, reason=code)
        print(json.dumps({"status": "failed", "code": code, "detail": str(error)}))
        return 1

    _analysis_output(
        status="acceptance_passed",
        ledger=ledger,
        verified=verified,
        policy=policy,
        score=score,
        acceptance=acceptance,
        include_frames=True,
    )
    print(
        json.dumps(
            {
                "destination_repository": args.repository,
                "outcomes": [
                    {"candidate_id": outcome.candidate_id, "state": outcome.state, "issue_number": outcome.issue_record.issue_number, "html_url": outcome.issue_record.html_url}
                    for outcome in outcomes
                ],
            },
            indent=2,
        )
    )
    return 0


def publish_main(args: argparse.Namespace) -> int:
    try:
        source_sha256 = file_sha256(args.video)
        ledger = RunLedger.load(args.output / source_sha256 / "ledger.json")
        verified_attempts = [attempt for attempt in ledger.attempts if attempt.get("status") == "verified"]
        if not verified_attempts:
            raise LedgerInvalid("publish requires a verified analysis attempt")
        policy_data = verified_attempts[-1].get("policy_result")
        if not isinstance(policy_data, dict):
            raise LedgerInvalid("publish requires a persisted policy result")
        policy = PolicyResult.model_validate(policy_data)
        canonical_issue_selections = parse_canonical_issue_selections(args.canonical_selection)
        token = os.environ.get("GITHUB_TOKEN", "")
        github = GitHubIssueClient(token)
        outcomes = WriteCoordinator(ledger, github).review_and_publish(
            policy.results,
            source_sha256=source_sha256,
            destination_repository=args.repository,
            attempt_id=str(verified_attempts[-1]["attempt_id"]),
            decision_fn=lambda candidate: terminal_review_decision(
                candidate,
                emit=lambda text: print(text, file=sys.stderr),
            ),
            operator_label=args.operator_label,
            reconsider=args.reconsider,
            retry_failed=args.retry_failed,
            retry_uncertain=args.retry_uncertain,
            confirm_no_issue=args.confirm_no_issue,
            canonical_issue_selections=canonical_issue_selections,
        )
    except ExternalWriteFailure as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": error.code,
                    "candidate_id": error.candidate_id,
                    "destination_repository": error.destination_repository,
                    "candidate_snapshot_hash": error.candidate_snapshot_hash,
                    "payload_hash": error.payload_hash,
                    "stage": error.stage,
                    "remote_status": error.remote_status,
                    "remote_error": error.remote_error,
                    "next_action": error.next_action,
                    "detail": error.detail,
                }
            )
        )
        return 1
    except (LedgerInvalid, LedgerLocked, OSError, ValueError) as error:
        code = "ledger_locked" if isinstance(error, LedgerLocked) else "publish_invalid"
        print(json.dumps({"status": "failed", "code": code, "detail": str(error)}))
        return 1

    status = "completed" if outcomes else "stopped"
    print(
        json.dumps(
            {
                "status": status,
                "ledger": str(ledger.path),
                "destination_repository": args.repository,
                "outcomes": [
                    {
                        "candidate_id": outcome.candidate_id,
                        "state": outcome.state,
                        "issue_number": outcome.issue_record.issue_number,
                        "html_url": outcome.issue_record.html_url,
                    }
                    for outcome in outcomes
                ],
            },
            indent=2,
        )
    )
    return 0


def parse_canonical_issue_selections(values: Sequence[str]) -> dict[str, int]:
    selections: dict[str, int] = {}
    for value in values:
        candidate_id, separator, issue_number_text = value.partition("=")
        if not separator or not candidate_id or not issue_number_text:
            raise ValueError("canonical selections must use CANDIDATE_ID=ISSUE_NUMBER")
        try:
            issue_number = int(issue_number_text)
        except ValueError as error:
            raise ValueError(f"canonical Issue number is not an integer: {issue_number_text}") from error
        if issue_number <= 0:
            raise ValueError("canonical Issue number must be positive")
        previous = selections.get(candidate_id)
        if previous is not None and previous != issue_number:
            raise ValueError(f"canonical Candidate selection is duplicated: {candidate_id}")
        selections[candidate_id] = issue_number
    return selections


if __name__ == "__main__":
    raise SystemExit(main())
