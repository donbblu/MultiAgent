from __future__ import annotations

import argparse
from pathlib import Path

from coding_workflow import FixedCodingEvaluationRunner, FixedCodingSuite


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    demo_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Run deterministic Core Coding fixture calibration."
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=demo_root / "coding_eval" / "v1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=demo_root / ".runs" / "core-coding-eval" / "calibration.json",
    )
    parser.add_argument(
        "--trusted-local-execution",
        action="store_true",
        help="明确允许本次评测执行受控的本地验证命令；默认拒绝",
    )
    args = parser.parse_args(argv)
    if not args.trusted_local_execution:
        parser.error(
            "固定评测会执行本地验证；必须显式提供 "
            "--trusted-local-execution"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = FixedCodingEvaluationRunner(
        FixedCodingSuite.load(args.suite),
        trusted_local_execution=args.trusted_local_execution,
    ).run()
    output = report.write_json(args.output)
    summary = report.summary()
    print(
        f"calibration_passed={str(report.calibration_passed).lower()} "
        f"tasks={summary['task_count']} trials={summary['trial_count']} "
        f"report={output}"
    )
    return 0 if report.calibration_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
