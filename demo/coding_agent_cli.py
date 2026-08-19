from __future__ import annotations

import argparse
import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Any

from coding_workflow.model import ModelClientFactory, load_env_file
from coding_workflow.models import TaskContext
from coding_workflow.policy import CommandPolicy, CommandPolicyError
from coding_workflow.workspace import ProjectWorkspace
from coding_workflow.harness import LifecycleController
from coding_workflow.dag_runner import run_dag_task


ROOT = Path(__file__).parent.resolve()
OUTPUT_ROOT = ROOT / "agent-output"
DEFAULT_ALLOWED_PATHS = ["*.py", "tests/*.py", "README.md"]
DEFAULT_VERIFY = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]


@dataclass(frozen=True)
class CodingRun:
    task: TaskContext
    output: Path
    provider: str
    model: str
    engine: str = "dag"


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
) -> CodingRun:
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
    )
    return CodingRun(
        dag_result.task, output, model_config.provider, model_config.model, "dag"
    )


def main() -> int:
    args = build_parser().parse_args()
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
        )
    except (ValueError, CommandPolicyError) as exc:
        raise SystemExit(str(exc)) from exc
    result = run.task

    print(f"任务: {result.task_id}")
    print(f"供应商: {run.provider}")
    print(f"模型: {run.model}")
    print(f"执行引擎: {run.engine}")
    print(f"状态: {result.state.value}")
    print(f"尝试次数: {result.attempt}")
    print(f"输出目录: {run.output}")
    for event in result.history:
        print(f"- {event}")
    return 0 if result.state.value == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
