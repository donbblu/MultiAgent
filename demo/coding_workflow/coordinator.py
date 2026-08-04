from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .agents import CodingAgent, ReviewAgent, VerificationAgent
from .models import ReviewResult, TaskContext, TaskState, VerificationResult
from .recording import RunRecorder
from .results import ResultEnvelope
from .roles import FIXER, IMPLEMENTER, PLANNER, REVIEWER, TESTER, RoleRegistry, DEFAULT_ROLES


class Coordinator:
    """唯一掌握流程控制权的 Agent。"""

    def __init__(
        self,
        coding_agent: CodingAgent,
        verification_agent: VerificationAgent,
        max_attempts: int = 3,
        recorder: RunRecorder | None = None,
        roles: RoleRegistry = DEFAULT_ROLES,
        review_agent: ReviewAgent | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须大于 0")
        self.coding_agent = coding_agent
        self.verification_agent = verification_agent
        self.max_attempts = max_attempts
        self.recorder = recorder
        self.roles = roles
        self.review_agent = review_agent

    def _assign_role(self, task: TaskContext, role_name: str) -> None:
        role = self.roles.get(role_name)
        task.assign_role(role)
        self._record(task, "role_assigned", role.model_input())

    def _record(self, task: TaskContext, event: str, payload: object) -> None:
        if self.recorder:
            self.recorder.record(task.task_id, event, payload)
            self.recorder.snapshot(task)

    def _transition(self, task: TaskContext, state: TaskState, note: str) -> None:
        task.transition(state, note)
        self._record(task, "state_transition", {"state": state.value, "note": note})

    def _run_parallel_quality_stage(
        self, task: TaskContext
    ) -> tuple[VerificationResult, ReviewResult | None]:
        tester = self.roles.get(TESTER.name)
        reviewer = self.roles.get(REVIEWER.name)
        task.active_role = None
        task.role_history.append(tester.name)
        roles = [tester.name]
        if self.review_agent:
            task.role_history.append(reviewer.name)
            roles.append(reviewer.name)
        self._record(task, "parallel_stage_started", {"roles": roles})
        stage_version = task.version

        with ThreadPoolExecutor(max_workers=len(roles), thread_name_prefix="quality") as pool:
            verification_future = pool.submit(self.verification_agent.run, task)
            review_future = pool.submit(self.review_agent.run, task) if self.review_agent else None
            verification_envelope = ResultEnvelope.create(
                task.task_id,
                stage_version,
                tester.name,
                "verification",
                verification_future.result(),
            )
            review_envelope = (
                ResultEnvelope.create(
                    task.task_id,
                    stage_version,
                    reviewer.name,
                    "review",
                    review_future.result(),
                )
                if review_future
                else None
            )

        verification_envelope.validate_for(task.task_id, stage_version)
        if review_envelope:
            review_envelope.validate_for(task.task_id, stage_version)
        self._record(task, "result_envelope", verification_envelope)
        if review_envelope:
            self._record(task, "result_envelope", review_envelope)
        return verification_envelope.payload, (
            review_envelope.payload if review_envelope else None
        )

    def run(self, task: TaskContext) -> TaskContext:
        if not task.objective.strip():
            self._transition(task, TaskState.FAILED, "用户目标不能为空")
            return task
        if not task.acceptance_criteria:
            self._transition(task, TaskState.FAILED, "至少需要一条验收标准")
            return task

        self._record(task, "task_started", task.model_input())
        self._assign_role(task, PLANNER.name)
        self._transition(task, TaskState.PLANNING, "已确认目标与验收标准")
        while task.attempt < self.max_attempts:
            task.attempt += 1
            self._assign_role(
                task, IMPLEMENTER.name if task.attempt == 1 else FIXER.name
            )
            self._transition(task, TaskState.IMPLEMENTING, f"开始第 {task.attempt} 次实现")
            task.implementation = self.coding_agent.run(task)
            self._record(task, "implementation", task.implementation)
            if not task.implementation.success:
                error = task.implementation.error or task.implementation.summary
                task.feedback = [error]
                self._transition(task, TaskState.REWORK, f"实现失败: {error}")
                continue

            self._transition(task, TaskState.VERIFYING, "开始并行验证与独立审查")
            task.verification, task.review = self._run_parallel_quality_stage(task)
            self._record(task, "verification", task.verification)
            if task.review:
                self._record(task, "review", task.review)
            review_passed = task.review is None or task.review.passed
            if task.verification.passed and review_passed:
                task.feedback = []
                note = task.verification.summary
                if task.review:
                    note += f"；{task.review.summary}"
                self._transition(task, TaskState.COMPLETED, note)
                return task

            task.feedback = list(task.verification.feedback)
            if task.review:
                task.feedback.extend(task.review.feedback)
            failures = []
            if not task.verification.passed:
                failures.append(task.verification.summary)
            if task.review and not task.review.passed:
                failures.append(task.review.summary)
            self._transition(task, TaskState.REWORK, "；".join(failures))

        self._transition(task, TaskState.FAILED, f"达到最大尝试次数 {self.max_attempts}")
        return task
