from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib import error as urlerror
from urllib import request as urlrequest

from coding_workflow.model import ModelCapability, ModelClientFactory, load_env_file
from coding_workflow.visionforge import (
    BrowserProcessRunner,
    EvaluationConfig,
    EvaluationModelBudget,
    EvaluationSuite,
    ReferenceImageRenderer,
    RuntimeEvaluationTrialExecutor,
    VisionForgeLocalExecutionApprover,
    VisionForgeEvaluator,
    estimate_model_calls,
)


ROOT = Path(__file__).resolve().parent
SUITE_PATH = ROOT / "visionforge_eval" / "v1" / "suite.json"
RENDERER_PATH = ROOT / "visionforge_eval" / "render-reference.mjs"
TEMPLATE_PATH = ROOT / "visionforge_vue_template"


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VisionForge 固定三方案真实基线；默认只输出预算，不调用模型。"
    )
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        help="明确允许本次真实模型调用；缺少此参数时只做预算预检。",
    )
    parser.add_argument(
        "--trusted-local-execution",
        action="store_true",
        help="明确允许本次评测执行受控的本地命令；默认拒绝。",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--max-fix-attempts", type=int, default=2)
    parser.add_argument("--max-total-tokens", type=int, default=600_000)
    parser.add_argument("--vision-max-output-tokens", type=int, default=8_000)
    parser.add_argument("--run-id", default="")
    return parser.parse_args(argv)


def _required_file(variable: str) -> Path:
    value = os.environ.get(variable, "").strip()
    path = Path(value).resolve() if value else Path()
    if not value or not path.is_file():
        raise RuntimeError(f"缺少有效的 {variable}")
    return path


def _check_endpoint(base_url: str, provider: str) -> None:
    request = urlrequest.Request(base_url.rstrip("/") + "/", method="HEAD")
    try:
        with urlrequest.urlopen(request, timeout=10):
            return
    except urlerror.HTTPError:
        return
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"模型供应商 {provider} 端点不可达: {exc}") from exc


def _trial_runner_factory(
    *,
    node: Path,
    pnpm: Path,
) -> Callable[[Path], BrowserProcessRunner]:
    overrides = {"node": str(node), "pnpm": str(pnpm)}

    def create(project_root: Path) -> BrowserProcessRunner:
        return BrowserProcessRunner(
            executable_overrides=overrides,
            workspace_root=project_root,
        )

    return create


def _approver_factory(
    trusted_local_execution: bool,
) -> Callable[[], VisionForgeLocalExecutionApprover]:
    if type(trusted_local_execution) is not bool:
        raise TypeError("trusted_local_execution 必须是真正的 bool")

    def create() -> VisionForgeLocalExecutionApprover:
        return VisionForgeLocalExecutionApprover(trusted_local_execution)

    return create


class _ProgressExecutor:
    def __init__(
        self, executor: RuntimeEvaluationTrialExecutor, total_trials: int
    ) -> None:
        self.executor = executor
        self.total_trials = total_trials
        self.completed = 0

    def execute(self, **kwargs):
        task = kwargs["task"]
        variant = kwargs["variant"]
        print(
            f"[trial:start] {task.task_id} / {variant.value}",
            flush=True,
        )
        result = self.executor.execute(**kwargs)
        self.completed += 1
        print(
            f"[trial:end] {self.completed}/{self.total_trials} "
            f"status={result.status} "
            f"delivery={result.delivery_passed} tokens={result.total_tokens}",
            flush=True,
        )
        return result


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if type(args.trusted_local_execution) is not bool:
        raise TypeError("trusted_local_execution 必须是真正的 bool")
    if args.confirm_real_calls and not args.trusted_local_execution:
        raise RuntimeError(
            "真实 VisionForge 评测还必须显式提供 "
            "--trusted-local-execution；拒绝发生在环境、Suite、模型和浏览器预检前"
        )
    load_env_file(ROOT / ".env")
    suite = EvaluationSuite.load(SUITE_PATH)
    estimate = estimate_model_calls(
        task_count=len(suite.tasks),
        repetitions=args.repetitions,
        max_fix_attempts=args.max_fix_attempts,
    )
    text_config = ModelClientFactory.config_from_env()
    vision_config = ModelClientFactory.vision_config_from_env()
    vision_config = replace(
        vision_config,
        include_max_tokens=True,
        max_tokens=args.vision_max_output_tokens,
    )
    config = EvaluationConfig(
        text_config.provider,
        text_config.model,
        "visionforge-mvp-1.0",
        repetitions=args.repetitions,
        max_fix_attempts=args.max_fix_attempts,
        max_output_tokens=text_config.max_tokens,
        vision_model_provider=vision_config.provider,
        vision_model_name=vision_config.model,
        max_model_calls=estimate.total_calls,
        max_total_tokens=args.max_total_tokens,
        vision_max_output_tokens=vision_config.max_tokens,
    )
    preflight = {
        "will_call_external_models": bool(args.confirm_real_calls),
        "will_execute_local_commands": bool(
            args.confirm_real_calls and args.trusted_local_execution
        ),
        "local_execution_approved": args.trusted_local_execution,
        "suite": {
            "id": suite.suite_id,
            "version": suite.version,
            "tasks": len(suite.tasks),
            "content_sha256": suite.content_sha256,
        },
        "models": config.to_dict()["models"],
        "call_estimate": estimate.to_dict(),
        "max_total_tokens": config.max_total_tokens,
    }
    print(json.dumps(preflight, ensure_ascii=False, indent=2))
    if not args.confirm_real_calls:
        return 0

    _check_endpoint(text_config.base_url, text_config.provider)
    _check_endpoint(vision_config.base_url, vision_config.provider)
    print("[preflight] 模型端点可达", flush=True)
    node = _required_file("VISIONFORGE_NODE")
    pnpm = _required_file("VISIONFORGE_PNPM")
    required_text = frozenset({
        ModelCapability.TEXT,
        ModelCapability.TOOL_CALLING,
        ModelCapability.STRUCTURED_OUTPUT,
    })
    required_vision = frozenset({
        ModelCapability.TEXT,
        ModelCapability.VISION,
        ModelCapability.STRUCTURED_OUTPUT,
    })
    text_client = ModelClientFactory.create(
        text_config, required_capabilities=required_text
    )
    vision_client = ModelClientFactory.create(
        vision_config, required_capabilities=required_vision
    )
    run_id = args.run_id.strip() or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in run_id):
        raise RuntimeError("run-id 只允许字母、数字、连字符和下划线")
    run_root = (ROOT / ".runs" / "visionforge-eval" / run_id).resolve()
    if run_root.exists():
        raise RuntimeError(f"评测 Run 已存在: {run_id}")
    run_root.mkdir(parents=True)
    runner_factory = _trial_runner_factory(node=node, pnpm=pnpm)
    approver_factory = _approver_factory(args.trusted_local_execution)
    renderer_runner = BrowserProcessRunner(
        executable_overrides={"node": str(node)},
        workspace_root=RENDERER_PATH.parent,
    )
    budget = EvaluationModelBudget(
        max_model_calls=config.max_model_calls,
        max_total_tokens=config.max_total_tokens,
    )
    executor = RuntimeEvaluationTrialExecutor(
        template_root=TEMPLATE_PATH,
        runtime_root=run_root,
        runner_factory=runner_factory,
        text_client=text_client,
        vision_client=vision_client,
        budget=budget,
        approver_factory=approver_factory,
    )
    evaluator = VisionForgeEvaluator(
        suite,
        config,
        ReferenceImageRenderer(renderer_runner, RENDERER_PATH),
        _ProgressExecutor(
            executor, len(suite.tasks) * len(estimate.by_variant) * args.repetitions
        ),
        run_root,
    )
    report = evaluator.run()
    report_path = run_root / "report.json"
    report.write(report_path)
    (run_root / "budget.json").write_text(
        json.dumps(budget.snapshot().to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "report": str(report_path),
        "budget": budget.snapshot().to_dict(),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
