from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from coding_workflow import FixedCodingSuite
from coding_workflow.coding_ablation_execution import (
    CoreAblationExperimentConfig,
    build_core_ablation_preflight,
    run_real_model_ablation,
    write_core_ablation_run_bundle,
)
from coding_workflow.model import ModelClientFactory, load_env_file


ROOT = Path(__file__).resolve().parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Core Coding 三方案真实消融；默认只输出零网络 preflight，"
            "不读取 .env、不调用模型。"
        )
    )
    parser.add_argument(
        "--suite", type=Path, default=ROOT / "coding_eval" / "v1"
    )
    parser.add_argument("--provider", default="dashscope")
    parser.add_argument("--model", default="qwen3.7-plus")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--max-output-tokens", type=int, default=4_000)
    parser.add_argument("--max-tokens-per-call", type=int, default=30_000)
    parser.add_argument("--max-total-tokens", type=int, default=300_000)
    parser.add_argument("--max-context-chars", type=int, default=60_000)
    parser.add_argument("--max-file-chars", type=int, default=20_000)
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        help="允许访问模型供应商；还必须提供匹配的 preflight SHA-256。",
    )
    parser.add_argument(
        "--confirm-preflight-sha256",
        default="",
        help="绑定用户已审阅的 preflight；摘要不匹配时拒绝调用。",
    )
    return parser.parse_args(argv)


def _model_config(args: argparse.Namespace):
    return ModelClientFactory.config_for_provider(
        args.provider,
        model=args.model,
        base_url=args.base_url or None,
        api_key_env=args.api_key_env or None,
        max_tokens=args.max_output_tokens,
        max_retries=0,
        temperature=0.0,
        enforce_max_tokens=True,
    )


def _safe_run_id(raw: str) -> str:
    value = raw.strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if any(character not in allowed for character in value):
        raise RuntimeError("run-id 只允许字母、数字、连字符和下划线")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    suite = FixedCodingSuite.load(args.suite)
    model_config = _model_config(args)
    experiment = CoreAblationExperimentConfig.from_model_config(
        model_config,
        task_count=len(suite.tasks),
        max_tokens_per_call=args.max_tokens_per_call,
        max_total_tokens=args.max_total_tokens,
    )
    experiment = CoreAblationExperimentConfig(
        **{
            **dict(experiment.to_dict()),
            "max_context_chars": args.max_context_chars,
            "max_file_chars": args.max_file_chars,
        }
    )
    experiment.validate(model_config)
    preflight = build_core_ablation_preflight(suite, experiment)
    print(json.dumps(dict(preflight.to_dict()), ensure_ascii=False, indent=2))
    if not args.confirm_real_calls:
        return 0
    if args.confirm_preflight_sha256 != preflight.digest:
        raise RuntimeError(
            "真实调用授权缺少或 preflight SHA-256 不匹配"
        )

    # Credential file and key existence are intentionally checked only after
    # the user-bound authorization digest has passed.
    load_env_file(ROOT / ".env")
    if not os.environ.get(model_config.api_key_env, "").strip():
        raise RuntimeError(
            f"缺少模型凭据环境变量 {model_config.api_key_env}"
        )
    run_id = _safe_run_id(args.run_id)
    run_root = (
        ROOT / ".runs" / "core-coding-ablation" / run_id
    ).resolve()
    if run_root.exists():
        raise RuntimeError(f"评测 Run 已存在: {run_id}")

    execution = run_real_model_ablation(suite, model_config, experiment)
    paths = write_core_ablation_run_bundle(
        run_root, preflight=preflight, execution=execution
    )
    print(json.dumps({
        "run_id": run_id,
        "paths": dict(paths),
        "budget": dict(execution.budget),
        "summary": dict(execution.report.summary()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
