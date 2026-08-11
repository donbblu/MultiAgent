from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from .agents import CodingAgent, ReviewAgent, VerificationAgent
from .communication import AgentMessage, MessageType
from .models import ReviewResult, TaskContext, TaskState, VerificationResult
from .recording import RunRecorder
from .results import ResultEnvelope
from .roles import FIXER, IMPLEMENTER, PLANNER, REVIEWER, TESTER, RoleRegistry, DEFAULT_ROLES
from .harness import (
    CancellationToken,
    LifecycleController,
    LifecycleState,
    TaskCancelledError,
    WorkerRegistry,
    WorkflowSpec,
    coding_workflow_spec,
)


class CodingHarness:
    """Coding 工作流的确定性控制面。"""

    def __init__(
        self,
        coding_agent: CodingAgent,
        verification_agent: VerificationAgent,
        max_attempts: int = 3,
        recorder: RunRecorder | None = None,
        roles: RoleRegistry = DEFAULT_ROLES,
        review_agent: ReviewAgent | None = None,
        workflow: WorkflowSpec | None = None,
        workers: WorkerRegistry | None = None,
        cancellation: CancellationToken | None = None,
        lifecycle: LifecycleController | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须大于 0")
        self.coding_agent = coding_agent
        self.verification_agent = verification_agent
        self.max_attempts = max_attempts
        self.recorder = recorder
        self.roles = roles
        self.review_agent = review_agent
        self.workflow = workflow or coding_workflow_spec()
        self.workers = workers or WorkerRegistry()
        if workers is None:
            self.workers.register(IMPLEMENTER.name, coding_agent)
            self.workers.register(FIXER.name, coding_agent)
            self.workers.register(TESTER.name, verification_agent)
            if review_agent:
                self.workers.register(REVIEWER.name, review_agent)
        if lifecycle is not None and cancellation is not None:
            raise ValueError("lifecycle 与 cancellation 不能同时传入")
        self.lifecycle = lifecycle or (
            cancellation.controller if cancellation else LifecycleController()
        )
        self.cancellation = CancellationToken(self.lifecycle)

    def _role_for_node(self, node_name: str) -> str:
        return self.workflow.node(node_name).role

    def _checkpoint(self, task: TaskContext) -> None:
        try:
            self.lifecycle.checkpoint()
        except TaskCancelledError as exc:
            if task.state not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
                self._transition(task, TaskState.CANCELLED, str(exc))
                self._send_message(task, "coordinator", "user", MessageType.FINAL, str(exc))
            self.lifecycle.mark_cancelled(str(exc))
            raise

    def _assign_role(self, task: TaskContext, role_name: str) -> None:
        role = self.roles.get(role_name)
        task.assign_role(role)
        self._record(task, "role_assigned", role.model_input())
        self._send_message(
            task,
            "coordinator",
            role.name,
            MessageType.HANDOFF,
            f"将任务交给 {role.name}",
            {"objective": role.objective, "attempt": task.attempt},
        )

    def _record(self, task: TaskContext, event: str, payload: object) -> None:
        if self.recorder:
            self.recorder.record(task.task_id, event, payload)
            self.recorder.snapshot(task)

    def _transition(self, task: TaskContext, state: TaskState, note: str) -> None:
        task.transition(state, note)
        self._record(task, "state_transition", {"state": state.value, "note": note})

    def _send_message(
        self,
        task: TaskContext,
        sender: str,
        recipient: str,
        message_type: MessageType,
        summary: str,
        payload: dict[str, object] | None = None,
        correlation_id: str = "",
    ) -> AgentMessage:
        message = AgentMessage.create(
            task_id=task.task_id,
            task_version=task.version,
            sender=sender,
            recipient=recipient,
            message_type=message_type,
            summary=summary,
            payload=payload,
            correlation_id=correlation_id,
        )
        self._record(task, "agent_message", message)
        return message

    def _run_parallel_quality_stage(
        self, task: TaskContext
    ) -> tuple[VerificationResult, ReviewResult | None]:
        tester = self.roles.get(self._role_for_node("test"))
        reviewer = self.roles.get(self._role_for_node("review"))
        task.active_role = None
        task.role_history.append(tester.name)
        roles = [tester.name]
        review_worker = self.workers.resolve(reviewer.name, required=False)
        if review_worker:
            task.role_history.append(reviewer.name)
            roles.append(reviewer.name)
        self._record(task, "parallel_stage_started", {"roles": roles})
        stage_version = task.version
        stage_correlation = uuid4().hex
        for role_name in roles:
            self._send_message(
                task,
                "coordinator",
                role_name,
                MessageType.HANDOFF,
                "开始并行质量检查",
                {"stage": "quality", "roles": roles},
                stage_correlation,
            )

        with ThreadPoolExecutor(max_workers=len(roles), thread_name_prefix="quality") as pool:
            verification_worker = self.workers.resolve(tester.name)
            verification_future = pool.submit(verification_worker.run, task)
            review_future = pool.submit(review_worker.run, task) if review_worker else None
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
        self._send_message(
            task,
            tester.name,
            "coordinator",
            MessageType.RESULT,
            verification_envelope.payload.summary,
            {
                "result_id": verification_envelope.result_id,
                "result_type": verification_envelope.result_type,
                "passed": verification_envelope.payload.passed,
                "feedback": verification_envelope.payload.feedback,
            },
            stage_correlation,
        )
        if review_envelope:
            self._record(task, "result_envelope", review_envelope)
            self._send_message(
                task,
                reviewer.name,
                "coordinator",
                MessageType.RESULT,
                review_envelope.payload.summary,
                {
                    "result_id": review_envelope.result_id,
                    "result_type": review_envelope.result_type,
                    "passed": review_envelope.payload.passed,
                    "feedback": review_envelope.payload.feedback,
                },
                stage_correlation,
            )
        return verification_envelope.payload, (
            review_envelope.payload if review_envelope else None
        )

    def run(self, task: TaskContext) -> TaskContext:
        try:
            return self._run_task(task)
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            if task.state not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
                self._transition(task, TaskState.FAILED, f"未处理异常: {reason}")
            if self.lifecycle.state is LifecycleState.RUNNING:
                self.lifecycle.mark_failed(reason)
            raise

    def _run_task(self, task: TaskContext) -> TaskContext:
        try:
            self.lifecycle.checkpoint()
            if self.lifecycle.state in {LifecycleState.CREATED, LifecycleState.QUEUED}:
                self.lifecycle.mark_running()
        except TaskCancelledError as exc:
            self._transition(task, TaskState.CANCELLED, str(exc))
            self.lifecycle.mark_cancelled(str(exc))
            return task
        if not task.objective.strip():
            self._transition(task, TaskState.FAILED, "用户目标不能为空")
            self._send_message(task, "coordinator", "user", MessageType.FINAL, "任务失败：用户目标不能为空")
            self.lifecycle.mark_failed("用户目标不能为空")
            return task
        if not task.acceptance_criteria:
            self._transition(task, TaskState.FAILED, "至少需要一条验收标准")
            self._send_message(task, "coordinator", "user", MessageType.FINAL, "任务失败：缺少验收标准")
            self.lifecycle.mark_failed("缺少验收标准")
            return task

        self._record(task, "task_started", task.model_input())
        self._send_message(
            task,
            "user",
            "coordinator",
            MessageType.REQUEST,
            task.objective,
            {"acceptance_criteria": task.acceptance_criteria},
        )
        self._assign_role(task, self._role_for_node("plan"))
        self._transition(task, TaskState.PLANNING, "已确认目标与验收标准")
        while task.attempt < self.max_attempts:
            try:
                self._checkpoint(task)
            except TaskCancelledError:
                return task
            task.attempt += 1
            implementation_role = (
                self._role_for_node("implement")
                if task.attempt == 1
                else self._role_for_node("fix")
            )
            self._assign_role(
                task, implementation_role
            )
            self._transition(task, TaskState.IMPLEMENTING, f"开始第 {task.attempt} 次实现")
            coding_worker = self.workers.resolve(implementation_role)
            task.implementation = coding_worker.run(task)
            try:
                self._checkpoint(task)
            except TaskCancelledError:
                return task
            self._record(task, "implementation", task.implementation)
            producer = task.active_role.name if task.active_role else IMPLEMENTER.name
            self._send_message(
                task,
                producer,
                "coordinator",
                MessageType.RESULT,
                task.implementation.summary,
                {
                    "success": task.implementation.success,
                    "changed_files": task.implementation.changed_files,
                    "error": task.implementation.error or "",
                },
            )
            if not task.implementation.success:
                error = task.implementation.error or task.implementation.summary
                task.feedback = [error]
                self._transition(task, TaskState.REWORK, f"实现失败: {error}")
                self._send_message(
                    task,
                    "coordinator",
                    FIXER.name,
                    MessageType.FEEDBACK,
                    "实现失败，需要返工",
                    {"feedback": task.feedback, "next_attempt": task.attempt + 1},
                )
                continue

            self._transition(task, TaskState.VERIFYING, "开始并行验证与独立审查")
            task.verification, task.review = self._run_parallel_quality_stage(task)
            try:
                self._checkpoint(task)
            except TaskCancelledError:
                return task
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
                self._send_message(
                    task,
                    "coordinator",
                    "user",
                    MessageType.FINAL,
                    note,
                    {"state": task.state.value, "attempts": task.attempt},
                )
                self.lifecycle.mark_completed()
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
            self._send_message(
                task,
                "coordinator",
                FIXER.name,
                MessageType.FEEDBACK,
                "质量检查未通过，需要返工",
                {"feedback": task.feedback, "next_attempt": task.attempt + 1},
            )

        self._transition(task, TaskState.FAILED, f"达到最大尝试次数 {self.max_attempts}")
        self._send_message(
            task,
            "coordinator",
            "user",
            MessageType.FINAL,
            f"任务失败：达到最大尝试次数 {self.max_attempts}",
            {"state": task.state.value, "attempts": task.attempt},
        )
        self.lifecycle.mark_failed(f"达到最大尝试次数 {self.max_attempts}")
        return task


# 保留旧入口，避免现有 CLI、Web 和调用方在架构迁移期间中断。
Coordinator = CodingHarness
