from __future__ import annotations

from .agents import CodingAgent, VerificationAgent
from .models import TaskContext, TaskState
from .recording import RunRecorder


class Coordinator:
    """唯一掌握流程控制权的 Agent。"""

    def __init__(
        self,
        coding_agent: CodingAgent,
        verification_agent: VerificationAgent,
        max_attempts: int = 3,
        recorder: RunRecorder | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须大于 0")
        self.coding_agent = coding_agent
        self.verification_agent = verification_agent
        self.max_attempts = max_attempts
        self.recorder = recorder

    def _record(self, task: TaskContext, event: str, payload: object) -> None:
        if self.recorder:
            self.recorder.record(task.task_id, event, payload)
            self.recorder.snapshot(task)

    def _transition(self, task: TaskContext, state: TaskState, note: str) -> None:
        task.transition(state, note)
        self._record(task, "state_transition", {"state": state.value, "note": note})

    def run(self, task: TaskContext) -> TaskContext:
        if not task.objective.strip():
            self._transition(task, TaskState.FAILED, "用户目标不能为空")
            return task
        if not task.acceptance_criteria:
            self._transition(task, TaskState.FAILED, "至少需要一条验收标准")
            return task

        self._record(task, "task_started", task.model_input())
        self._transition(task, TaskState.PLANNING, "已确认目标与验收标准")
        while task.attempt < self.max_attempts:
            task.attempt += 1
            self._transition(task, TaskState.IMPLEMENTING, f"开始第 {task.attempt} 次实现")
            task.implementation = self.coding_agent.run(task)
            self._record(task, "implementation", task.implementation)
            if not task.implementation.success:
                error = task.implementation.error or task.implementation.summary
                task.feedback = [error]
                self._transition(task, TaskState.REWORK, f"实现失败: {error}")
                continue

            self._transition(task, TaskState.VERIFYING, "开始独立验证")
            task.verification = self.verification_agent.run(task)
            self._record(task, "verification", task.verification)
            if task.verification.passed:
                task.feedback = []
                self._transition(task, TaskState.COMPLETED, task.verification.summary)
                return task

            task.feedback = task.verification.feedback
            self._transition(task, TaskState.REWORK, task.verification.summary)

        self._transition(task, TaskState.FAILED, f"达到最大尝试次数 {self.max_attempts}")
        return task
