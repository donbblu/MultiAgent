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
OUTPUT = ROOT / "bubble_sort_output"
ACCEPTANCE_COMMAND = ["python3", ".verification/test_bubble_sort.py"]


def install_trusted_acceptance_test(workspace: ProjectWorkspace) -> None:
    target = workspace.root / ".verification" / "test_bubble_sort.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import sys\n"
        "import unittest\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from bubble_sort import bubble_sort\n\n"
        "class BubbleSortAcceptanceTests(unittest.TestCase):\n"
        "    def test_unsorted_numbers(self):\n"
        "        self.assertEqual(bubble_sort([5, 1, 4, 2, 8]), [1, 2, 4, 5, 8])\n\n"
        "    def test_empty_and_single_item(self):\n"
        "        self.assertEqual(bubble_sort([]), [])\n"
        "        self.assertEqual(bubble_sort([7]), [7])\n\n"
        "    def test_duplicates_and_negatives(self):\n"
        "        self.assertEqual(bubble_sort([3, -1, 3, 0]), [-1, 0, 3, 3])\n\n"
        "    def test_does_not_mutate_input(self):\n"
        "        values = [2, 1]\n"
        "        bubble_sort(values)\n"
        "        self.assertEqual(values, [2, 1])\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main(verbosity=2)\n",
        encoding="utf-8",
    )


def main() -> None:
    load_env_file(ROOT / ".env")
    workspace = ProjectWorkspace(OUTPUT, command_timeout=20)
    install_trusted_acceptance_test(workspace)
    task = TaskContext(
        task_id="BUBBLE-SORT-001",
        objective="编写一个冒泡排序函数并将结果放在当前项目的隔离输出目录",
        user_request=(
            "为我写一个 Python 冒泡排序。创建 bubble_sort.py，提供 bubble_sort(values) 函数；"
            "返回从小到大的新列表，不修改传入列表。"
        ),
        acceptance_criteria=[
            "普通乱序数字能够升序排列",
            "支持空列表、单元素、重复值和负数",
            "不修改调用者传入的列表",
            "Runtime 独立验收测试全部通过",
        ],
        verification_commands=[ACCEPTANCE_COMMAND],
        project_root=str(OUTPUT),
        tech_stack={"language": "Python", "dependencies": "stdlib only"},
        constraints=["只使用 Python 标准库", "函数名必须为 bubble_sort"],
        allowed_paths=["bubble_sort.py", "README.md"],
        prohibited_actions=[
            "修改验收测试",
            "读取或输出密钥",
            "安装依赖",
            "执行命令",
            "访问隔离输出目录之外的文件",
        ],
    )
    verifier = CommandVerificationAgent(
        workspace,
        CommandPolicy(
            allowed_executables={"python3"}, allowed_commands=[ACCEPTANCE_COMMAND]
        ),
    )
    model_config = ModelClientFactory.config_from_env()
    result = Coordinator(
        WorkspaceCodingAgent(
            StructuredCodingBackend(ModelClientFactory.create(model_config)), workspace
        ),
        verifier,
        max_attempts=2,
        recorder=RunRecorder(ROOT / ".runs"),
    ).run(task)

    print(f"需求: {task.user_request}")
    print(f"状态: {result.state.value}")
    print(f"尝试次数: {result.attempt}")
    print(f"输出目录: {OUTPUT}")
    for event in result.history:
        print(f"- {event}")


if __name__ == "__main__":
    main()
