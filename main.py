import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from feedback_triage.analyze import AnalysisFailed, file_sha256, triage_recording
from feedback_triage.github import GitHubIssueClient
from feedback_triage.evaluation import load_ground_truth, score_policy_result
from feedback_triage.gemini_video import PROMPT
from feedback_triage.ledger import LedgerInvalid, LedgerLocked, RunLedger
from feedback_triage.models import PolicyResult
from feedback_triage.writes import ExternalWriteFailure, WriteCoordinator, terminal_review_decision


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
    return command_parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "publish":
        return publish_main(args)
    try:
        ground_truth = load_ground_truth(args.ground_truth) if args.ground_truth else None
        fixture_dir = args.ground_truth.parent if args.ground_truth else None
        prompt_path = args.prompt or (fixture_dir / "prompt.md" if fixture_dir else None)
        context_path = args.context or (fixture_dir / "project-context.md" if fixture_dir else None)
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path else None
        project_context = context_path.read_text(encoding="utf-8") if context_path else ""
        ground_truth_sha256 = file_sha256(args.ground_truth) if args.ground_truth else None
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "code": "invalid_fixture", "detail": str(error)}))
        return 1
    try:
        video_info, verified, policy, ledger = triage_recording(
            args.video,
            args.output,
            reanalyze=args.reanalyze,
            prompt=prompt if prompt is not None else PROMPT,
            project_context=project_context,
            fixture_version=ground_truth.fixture_version if ground_truth else None,
            ground_truth_sha256=ground_truth_sha256,
        )
    except AnalysisFailed as error:
        print(json.dumps({"status": "failed", "code": error.code, "detail": str(error)}))
        return 1
    except LedgerInvalid as error:
        print(json.dumps({"status": "failed", "code": "ledger_invalid", "detail": str(error)}))
        return 1
    score = (
        score_policy_result(
            policy,
            ground_truth,
            source_sha256=ledger.source_sha256,
            duration_seconds=video_info.duration_seconds,
        )
        if ground_truth is not None
        else None
    )
    status = "ready_for_review"
    if score is not None and not score.passed:
        status = "semantic_score_failed"
    attempts = ledger.attempts
    evidence_frames = (
        ledger.evidence_frames_for_attempt(str(attempts[-1]["attempt_id"])) if attempts else ()
    )
    print(
        json.dumps(
            {
                "status": status,
                "ledger": str(ledger.path),
                "processing_pair_count": verified.processing_pair_count,
                "analysis": verified.analysis.model_dump(mode="json"),
                "policy_result": policy.model_dump(mode="json"),
                "evidence_frames": [frame.model_dump(mode="json") for frame in evidence_frames],
                "score": score.model_dump(mode="json") if score is not None else None,
            },
            indent=2,
        )
    )
    return 0 if score is None or score.passed else 1


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
        )
    except ExternalWriteFailure as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": error.code,
                    "candidate_id": error.candidate_id,
                    "destination_repository": error.destination_repository,
                    "payload_hash": error.payload_hash,
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


if __name__ == "__main__":
    raise SystemExit(main())
