from __future__ import annotations

from .backends import StructuredCodingBackend
from .harness import TaskRunRequest, TaskRunResult
from .validation import PlanValidator, SchemaValidationError


class PlanningCodingWorker:
    """DAG 实现 Worker：只生成 Patch Artifact，不写共享 Workspace。"""

    def __init__(self, backend: StructuredCodingBackend, validator: PlanValidator | None = None) -> None:
        self.backend = backend
        self.validator = validator or PlanValidator()

    def run_task(self, request: TaskRunRequest) -> TaskRunResult:
        try:
            plan = self.backend.create_plan(request.memory)
            validation_task = request.parent
            original_paths = validation_task.allowed_paths
            validation_task.allowed_paths = list(request.task.write_scopes or original_paths)
            try:
                self.validator.validate(plan, validation_task)
            finally:
                validation_task.allowed_paths = original_paths
            return TaskRunResult(
                True, plan.summary,
                {name: plan for name in request.task.output_artifacts},
            )
        except (SchemaValidationError, ValueError) as exc:
            return TaskRunResult(False, "Patch 生成失败", error=str(exc))
