import argparse
import json
from pathlib import Path
from typing import Sequence

from feedback_triage.analyze import AnalysisFailed, analyze_recording


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="feedback-triage")
    subcommands = command_parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="analyze one owned synthetic Feedback Recording")
    analyze.add_argument("video", type=Path)
    analyze.add_argument("--output", type=Path, default=Path("output"))
    analyze.add_argument("--reanalyze", action="store_true", help="append a replacement attempt after reconciliation")
    return command_parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _, verified, ledger = analyze_recording(args.video, args.output, reanalyze=args.reanalyze)
    except AnalysisFailed as error:
        print(json.dumps({"status": "failed", "code": error.code, "detail": str(error)}))
        return 1
    print(
        json.dumps(
            {
                "status": "ready_for_review",
                "ledger": str(ledger.path),
                "processing_pair_count": verified.processing_pair_count,
                "analysis": verified.analysis.model_dump(mode="json"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
