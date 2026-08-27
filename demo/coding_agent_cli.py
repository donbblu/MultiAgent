from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Any

from coding_workflow.model import ModelClientFactory, load_env_file
from coding_workflow.models import TaskContext
from coding_workflow.policy import CommandPolicy, CommandPolicyError
from coding_workflow.workspace import ProjectWorkspace
from coding_workflow.harness import LifecycleController
from coding_workflow.dag_runner import run_dag_task
from coding_workflow.local_execution_approval import LocalExecutionApprover
from coding_workflow.local_execution import redact_text


ROOT = Path(__file__).parent.resolve()
OUTPUT_ROOT = ROOT / "agent-output"
DEFAULT_ALLOWED_PATHS = ["*.py", "tests/*.py", "README.md"]
DEFAULT_VERIFY = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
LOCAL_EXECUTION_REPORT_SCHEMA = "local-execution-cli-report/v1"


@dataclass(frozen=True)
class CodingRun:
    task: TaskContext
    output: Path
    provider: str
    model: str
    engine: str = "dag"


def _report_command(command: list[str] | tuple[str, ...]) -> list[str]:
    raw = [str(part) for part in command]
    lowered = " ".join(raw).lower()
    if "private key" in lowered or (
        "-----begin" in lowered and "private" in lowered
    ):
        return ["[REDACTED PRIVATE KEY COMMAND]"]

    sensitive_names = frozenset({
        "--api-key", "--apikey", "--access-token", "--token",
        "--password", "--passwd", "--secret", "api_key", "api-key",
        "access_token", "access-token", "token", "password", "passwd",
        "secret",
    })
    result: list[str] = []
    redact_next = False
    for part in raw:
        normalized = part.strip().lower()
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        if normalized in sensitive_names:
            result.append(redact_text(part))
            redact_next = True
            continue
        if normalized == "bearer":
            result.append("Bearer")
            redact_next = True
            continue
        matched_assignment = False
        for separator in ("=", ":"):
            if separator not in part:
                continue
            key, _value = part.split(separator, 1)
            if key.strip().lower() in sensitive_names:
                result.append(f"{key}{separator}[REDACTED]")
                matched_assignment = True
                break
        if not matched_assignment:
            result.append(redact_text(part))
    return result


def build_local_execution_report(
    *,
    approved: bool,
    verification_command: list[str],
    duration_ms: int,
    run: CodingRun | None = None,
    rejection_reason: str = "",
) -> dict[str, object]:
    """Build a token-free, user-visible projection of local execution."""
    if type(approved) is not bool:
        raise TypeError("approved 必须是真正的 bool")
    if type(duration_ms) is not int or duration_ms < 0:
        raise ValueError("duration_ms 必须是非负整数")

    report: dict[str, object] = {
        "schema": LOCAL_EXECUTION_REPORT_SCHEMA,
        "approval": {
            "requested": approved,
            "source": "cli_exact_bool",
            "opaque_token_exposed": False,
        },
        "requested_commands": [_report_command(verification_command)],
        "duration_ms": duration_ms,
        "spawn_count": 0 if not approved else None,
        "spawn_count_source": (
            "preflight_zero" if not approved else "not_instrumented"
        ),
        "terminal_execution_count": 0,
        "command_result_count": 0,
        "results": [],
    }
    if not approved:
        report.update({
            "status": "rejected_before_task",
            "task_outcome": "not_started",
            "reason": redact_text(
                rejection_reason
                or "缺少 --trusted-local-execution；模型、Workspace 与本地进程均未启动"
            ),
        })
        return report

    if run is None:
        report.update({
            "status": "not_reached",
            "task_outcome": "unknown",
            "reason": "任务没有返回可观察的本地执行结果",
        })
        return report

    verification = run.task.verification
    report["task_outcome"] = run.task.state.value
    if verification is None:
        report.update({
            "status": "not_reached",
            "reason": "任务未到达本地验证阶段",
        })
        return report

    results: list[dict[str, object]] = []
    terminal_execution_count = 0
    for result in verification.command_results:
        manifest = result.profile_manifest
        cleanup = result.cleanup_evidence
        profile_id = ""
        cleanup_status = ""
        cleanup_verified = False
        if isinstance(manifest, Mapping):
            profile_id = redact_text(str(manifest.get("profile_id", "")))
        if isinstance(cleanup, Mapping):
            cleanup_status = redact_text(str(cleanup.get("status", "")))
            cleanup_verified = cleanup.get("verified") is True
        terminal_evidence = bool(profile_id and cleanup_status)
        terminal_execution_count += int(terminal_evidence)
        results.append({
            "command": _report_command(result.command),
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "profile_id": profile_id,
            "terminal_execution_evidence": terminal_evidence,
            "cleanup_status": cleanup_status,
            "cleanup_verified": cleanup_verified,
            "cleanup_evidence_digest": redact_text(
                result.cleanup_evidence_digest
            ),
            "stdout": redact_text(result.stdout),
            "stderr": redact_text(result.stderr),
            "stdout_chars": result.stdout_chars,
            "stderr_chars": result.stderr_chars,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
        })
    report["results"] = results
    report["terminal_execution_count"] = terminal_execution_count
    report["command_result_count"] = len(results)
    if results and verification.passed:
        report["status"] = "terminal"
        report["reason"] = redact_text(verification.summary)
    elif results:
        report["status"] = "failed"
        report["reason"] = redact_text(verification.summary)
    else:
        report["status"] = "rejected"
        report["reason"] = redact_text(verification.summary)
    return report


def render_local_execution_report(
    report: Mapping[str, object],
    output_format: str,
) -> str:
    if output_format == "json":
        return json.dumps(
            dict(report), ensure_ascii=False, sort_keys=True, indent=2
        )
    if output_format == "text":
        approval = report.get("approval", {})
        approved = (
            approval.get("requested", False)
            if isinstance(approval, Mapping)
            else False
        )
        spawn_count = report.get("spawn_count")
        spawn_display = (
            "未直接计数" if spawn_count is None else str(spawn_count)
        )
        return "\n".join((
            "本地执行报告",
            f"- 状态: {report.get('status', 'unknown')}",
            f"- 明确批准: {str(bool(approved)).lower()}",
            f"- spawn 计数: {spawn_display}",
            f"- 终态执行证据: {report.get('terminal_execution_count', 0)}",
            f"- 命令结果数: {report.get('command_result_count', 0)}",
            f"- 耗时: {report.get('duration_ms', 0)} ms",
            f"- 说明: {report.get('reason', '')}",
        ))
    if output_format != "markdown":
        raise ValueError("报告格式必须是 text、json 或 markdown")

    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")

    approval = report.get("approval", {})
    approved = (
        approval.get("requested", False)
        if isinstance(approval, Mapping)
        else False
    )
    spawn_count = report.get("spawn_count")
    spawn_display = "未直接计数" if spawn_count is None else spawn_count
    lines = [
        "# 本地执行报告",
        "",
        f"- 状态：`{cell(report.get('status', 'unknown'))}`",
        f"- 明确批准：`{str(bool(approved)).lower()}`",
        f"- spawn 计数：`{cell(spawn_display)}`",
        f"- 终态执行证据：`{cell(report.get('terminal_execution_count', 0))}`",
        f"- 命令结果数：`{cell(report.get('command_result_count', 0))}`",
        f"- 耗时：`{cell(report.get('duration_ms', 0))} ms`",
        f"- 说明：{cell(report.get('reason', ''))}",
        "",
        "| 命令 | Profile | Exit | Timeout | Cleanup | Verified |",
        "|---|---|---:|---|---|---|",
    ]
    raw_results = report.get("results", [])
    result_rows = 0
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, Mapping):
                continue
            result_rows += 1
            lines.append(
                "| {command} | {profile} | {exit_code} | {timed_out} | "
                "{cleanup} | {verified} |".format(
                    command=cell(json.dumps(
                        item.get("command", []), ensure_ascii=False
                    )),
                    profile=cell(item.get("profile_id", "")),
                    exit_code=cell(item.get("exit_code", "")),
                    timed_out=cell(item.get("timed_out", False)),
                    cleanup=cell(item.get("cleanup_status", "")),
                    verified=cell(item.get("cleanup_verified", False)),
                )
            )
    if result_rows == 0:
        lines.append("| — | — | — | — | — | — |")
    return "\n".join(lines)


def safe_output_path(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise ValueError("--name 必须是不含目录分隔符的简单名称")
    if not name or name.startswith(".") or not all(
        char.isalnum() or char in {"-", "_"} for char in name
    ):
        raise ValueError("--name 只能包含字母、数字、连字符和下划线")
    output = (OUTPUT_ROOT / name).resolve()
    if not output.is_relative_to(OUTPUT_ROOT.resolve()):
        raise ValueError("输出目录越过安全边界")
    return output


def parse_command(value: str) -> list[str]:
    command = shlex.split(value)
    if not command:
        raise argparse.ArgumentTypeError("验证命令不能为空")
    if command[0] not in {"python3", "python", "pytest"}:
        raise argparse.ArgumentTypeError("当前只允许 python3、python 或 pytest 验证命令")
    if "-c" in command or "pip" in command or "install" in command:
        raise argparse.ArgumentTypeError("验证命令禁止 -c、pip 和 install")
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用 Multi-Agent 根据自然语言需求生成并验证 Python 项目。"
    )
    parser.add_argument("requirement", help="自然语言项目需求")
    parser.add_argument("--name", default="task", help="agent-output 下的输出目录名")
    parser.add_argument(
        "--criterion", action="append", default=[], help="补充验收标准，可重复"
    )
    parser.add_argument(
        "--allow", action="append", default=[], help="允许模型修改的路径模式，可重复"
    )
    parser.add_argument(
        "--verify",
        type=parse_command,
        default=None,
        help='验证命令，例如 "python3 -m unittest discover -s tests -v"',
    )
    parser.add_argument("--provider", default=None, help="模型供应商，默认读取 MODEL_PROVIDER")
    parser.add_argument("--model", default=None, help="模型名称，默认读取 MODEL_NAME")
    parser.add_argument(
        "--continue-existing",
        action="store_true",
        help="允许在已有输出目录中继续修改；默认拒绝覆盖已有任务",
    )
    parser.add_argument(
        "--trusted-local-execution",
        action="store_true",
        help="明确允许本次任务执行受控的本地验证命令；默认拒绝",
    )
    parser.add_argument(
        "--local-execution-report",
        choices=("text", "json", "markdown"),
        default="text",
        help="本地执行状态的可见报告格式，默认 text",
    )
    return parser


def run_requirement(
    requirement: str,
    name: str,
    *,
    criteria: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    verification_command: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    continue_existing: bool = False,
    event_listener: Callable[[dict[str, Any]], None] | None = None,
    lifecycle: LifecycleController | None = None,
    trusted_local_execution: bool = False,
) -> CodingRun:
    if type(trusted_local_execution) is not bool:
        raise TypeError("trusted_local_execution 必须是真正的 bool")
    if not requirement.strip():
        raise ValueError("需求不能为空")
    output = safe_output_path(name)
    if output.exists() and any(output.iterdir()) and not continue_existing:
        raise ValueError(f"输出目录已有内容: {output}")
    selected_paths = allowed_paths or DEFAULT_ALLOWED_PATHS
    selected_command = verification_command or DEFAULT_VERIFY
    policy = CommandPolicy(
        allowed_executables={"python3", "python", "pytest"},
        allowed_commands=[selected_command],
    )
    policy.validate(selected_command)

    load_env_file(ROOT / ".env")
    workspace = ProjectWorkspace(output, command_timeout=60)
    selected_criteria = criteria or [
        "实现用户描述的功能",
        "覆盖正常输入和关键边界情况",
        "在 tests/ 中提供 unittest 自动化测试",
        "全部验证命令通过且测试数量大于零",
    ]
    task_hash = hashlib.sha256(
        f"{name}:{requirement}".encode("utf-8")
    ).hexdigest()[:10]
    task = TaskContext(
        task_id=f"UI-{task_hash}",
        objective=requirement,
        user_request=requirement,
        acceptance_criteria=selected_criteria,
        verification_commands=[selected_command],
        project_root=str(output),
        tech_stack={"language": "Python", "dependencies": "stdlib only"},
        constraints=[
            "只使用 Python 标准库",
            "必须在 tests/ 中提供 unittest 测试",
            "不要执行命令或安装依赖",
        ],
        allowed_paths=selected_paths,
        prohibited_actions=[
            "读取或输出密钥",
            "修改输出目录之外的文件",
            "访问网络",
            "安装依赖",
            "执行命令",
            "修改 .env、.git、.verification 或 .runs",
        ],
    )
    model_config = ModelClientFactory.config_from_env(provider, model)
    client = ModelClientFactory.create(model_config)
    dag_result = run_dag_task(
        task, client, workspace,
        memory_path=ROOT / ".runtime" / f"{task.task_id}.sqlite3",
        lifecycle=lifecycle, max_workers=3, command_policy=policy,
        event_listener=event_listener,
        approver_factory=lambda: LocalExecutionApprover(
            trusted_local_execution
        ),
    )
    return CodingRun(
        dag_result.task, output, model_config.provider, model_config.model, "dag"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected_command = args.verify or DEFAULT_VERIFY
    started = time.monotonic()
    if not args.trusted_local_execution:
        report = build_local_execution_report(
            approved=False,
            verification_command=selected_command,
            duration_ms=0,
        )
        print(render_local_execution_report(
            report, args.local_execution_report
        ))
        return 2
    try:
        run = run_requirement(
            args.requirement,
            args.name,
            criteria=args.criterion or None,
            allowed_paths=args.allow or None,
            verification_command=args.verify,
            provider=args.provider,
            model=args.model,
            continue_existing=args.continue_existing,
            trusted_local_execution=args.trusted_local_execution,
        )
    except (ValueError, CommandPolicyError) as exc:
        raise SystemExit(str(exc)) from exc
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    result = run.task

    report = build_local_execution_report(
        approved=True,
        verification_command=selected_command,
        duration_ms=duration_ms,
        run=run,
    )
    if args.local_execution_report == "text":
        print(f"任务: {result.task_id}")
        print(f"供应商: {run.provider}")
        print(f"模型: {run.model}")
        print(f"执行引擎: {run.engine}")
        print(f"状态: {result.state.value}")
        print(f"尝试次数: {result.attempt}")
        print(f"输出目录: {run.output}")
        for event in result.history:
            print(f"- {event}")
    print(render_local_execution_report(
        report, args.local_execution_report
    ))
    return 0 if result.state.value == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
