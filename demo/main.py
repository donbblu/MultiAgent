from pathlib import Path

from coding_workflow.agents import (
    CommandVerificationAgent,
    DemoProjectBackend,
    WorkspaceCodingAgent,
)
from coding_workflow.coordinator import Coordinator
from coding_workflow.models import TaskContext
from coding_workflow.workspace import ProjectWorkspace
from coding_workflow.recording import RunRecorder


def main() -> None:
    output = Path(__file__).parent / "generated_project"
    workspace = ProjectWorkspace(output)
    task = TaskContext(
        task_id="TASK-001",
        objective="创建一个 Python 问候项目，支持姓名和空输入",
        acceptance_criteria=[
            "传入姓名时返回个性化问候",
            "空输入时返回默认问候",
            "自动化测试通过",
        ],
        verification_commands=[["python3", "-m", "unittest", "-v"]],
        user_request="请创建一个简单、可测试的 Python 问候项目",
        project_root=str(output),
        tech_stack={"language": "Python", "version": "3.10+"},
        constraints=["只使用 Python 标准库", "必须包含自动化测试"],
        allowed_paths=["*.py", "README.md"],
        prohibited_actions=["安装依赖", "访问网络", "修改项目目录之外的文件"],
        assumptions=["命令行环境中可以使用 python3"],
    )
    result = Coordinator(
        WorkspaceCodingAgent(DemoProjectBackend(), workspace),
        CommandVerificationAgent(workspace),
        recorder=RunRecorder(Path(__file__).parent / ".runs"),
    ).run(task)

    print(f"任务: {result.task_id}")
    print(f"最终状态: {result.state.value}")
    print(f"生成目录: {output}")
    print("执行历史:")
    for event in result.history:
        print(f"- {event}")


if __name__ == "__main__":
    main()
