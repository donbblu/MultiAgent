from __future__ import annotations

from pathlib import Path

from coding_workflow.agents import CommandVerificationAgent, WorkspaceCodingAgent
from coding_workflow.coordinator import Coordinator
from coding_workflow.backends import StructuredCodingBackend
from coding_workflow.model import ModelClientFactory, load_env_file
from coding_workflow.models import TaskContext
from coding_workflow.policy import CommandPolicy
from coding_workflow.recording import RunRecorder
from coding_workflow.workspace import ProjectWorkspace


ROOT = Path(__file__).parent
OUTPUT = ROOT / "model_generated_project"
ACCEPTANCE_COMMAND = ["python3", ".verification/test_acceptance.py"]


def install_trusted_acceptance_test(workspace: ProjectWorkspace) -> None:
    """该文件由 Runtime 创建，模型无权修改。"""
    target = workspace.root / ".verification" / "test_acceptance.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import sys\n"
        "import unittest\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from app import greet\n\n"
        "class AcceptanceTests(unittest.TestCase):\n"
        "    def test_named_user(self):\n"
        "        self.assertEqual(greet('Model'), 'Hello, Model!')\n\n"
        "    def test_empty_user(self):\n"
        "        self.assertEqual(greet(''), 'Hello, stranger!')\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main(verbosity=2)\n",
        encoding="utf-8",
    )


def main() -> None:
    load_env_file(ROOT / ".env")
    workspace = ProjectWorkspace(OUTPUT, command_timeout=20)
    install_trusted_acceptance_test(workspace)
    task = TaskContext(
        task_id="MODEL-DEMO-001",
        objective="创建一个 Python 问候模块，支持姓名和空输入",
        user_request="创建 app.py，其中 greet(name) 对姓名返回 Hello, <name>!，空输入返回 Hello, stranger!",
        acceptance_criteria=[
            "greet('Model') 返回 Hello, Model!",
            "greet('') 返回 Hello, stranger!",
            "Runtime 提供的独立验收测试通过",
        ],
        verification_commands=[ACCEPTANCE_COMMAND],
        project_root=str(OUTPUT),
        tech_stack={"language": "Python", "dependencies": "stdlib only"},
        constraints=["只使用 Python 标准库", "实现必须位于 app.py"],
        allowed_paths=["app.py", "README.md"],
        prohibited_actions=[
            "修改验收测试",
            "读取或输出密钥",
            "安装依赖",
            "执行命令",
            "访问项目目录之外的文件",
        ],
    )
    config = ModelClientFactory.config_from_env()
    backend = StructuredCodingBackend(ModelClientFactory.create(config))
    verifier = CommandVerificationAgent(
        workspace,
        CommandPolicy(
            allowed_executables={"python3"}, allowed_commands=[ACCEPTANCE_COMMAND]
        ),
    )
    result = Coordinator(
        WorkspaceCodingAgent(backend, workspace),
        verifier,
        max_attempts=2,
        recorder=RunRecorder(ROOT / ".runs"),
    ).run(task)

    print(f"任务: {result.task_id}")
    print(f"模型: {config.model}")
    print(f"状态: {result.state.value}")
    print(f"尝试次数: {result.attempt}")
    print(f"生成目录: {OUTPUT}")
    for event in result.history:
        print(f"- {event}")


if __name__ == "__main__":
    main()
