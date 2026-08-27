from __future__ import annotations

import argparse
from pathlib import Path

from coding_workflow import (
    CodingAblationRunner,
    FixedCodingSuite,
    build_scripted_ablation_registry,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    demo_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Run the offline scripted Core Coding ablation dry-run."
    )
    parser.add_argument(
        "--suite", type=Path, default=demo_root / "coding_eval" / "v1"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            demo_root / ".runs" / "core-coding-eval" / "ablation-dry-run.json"
        ),
    )
    parser.add_argument(
        "--trusted-local-execution",
        action="store_true",
        help="明确允许本次消融执行受控的本地验证命令；默认拒绝",
    )
    args = parser.parse_args(argv)
    if not args.trusted_local_execution:
        parser.error(
            "固定消融会执行本地验证；必须显式提供 "
            "--trusted-local-execution"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    suite = FixedCodingSuite.load(args.suite)
    registry, _ = build_scripted_ablation_registry(suite)
    report = CodingAblationRunner(
        suite,
        registry,
        trusted_local_execution=args.trusted_local_execution,
    ).run()
    output = report.write_json(args.output)
    print(
        f"dry_run={str(report.dry_run).lower()} "
        f"tasks={len(suite.tasks)} trials={len(report.trials)} "
        f"external_model_calls="
        f"{sum(item.model_calls for item in report.trials)} "
        f"report={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
