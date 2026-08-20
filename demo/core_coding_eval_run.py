from __future__ import annotations

import argparse
from pathlib import Path

from coding_workflow import FixedCodingEvaluationRunner, FixedCodingSuite


def parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = FixedCodingEvaluationRunner(
        FixedCodingSuite.load(args.suite)
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
