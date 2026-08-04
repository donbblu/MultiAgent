from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath

from .models import ImplementationPlan, TaskContext


class SchemaValidationError(ValueError):
    pass


class PlanValidator:
    """模型输出进入文件系统前的确定性校验层。"""

    PROTECTED_PARTS = {".git", ".env", ".verification", ".runs"}

    def __init__(self, max_files: int = 20, max_content_chars: int = 200_000) -> None:
        self.max_files = max_files
        self.max_content_chars = max_content_chars

    def validate(self, plan: ImplementationPlan, task: TaskContext) -> None:
        if not isinstance(plan, ImplementationPlan):
            raise SchemaValidationError("后端必须返回 ImplementationPlan")
        if not plan.summary.strip():
            raise SchemaValidationError("实现计划缺少 summary")
        if len(plan.summary) > 2000:
            raise SchemaValidationError("实现计划摘要过长")
        if not plan.changes:
            raise SchemaValidationError("实现计划没有文件变更")
        if len(plan.changes) > self.max_files:
            raise SchemaValidationError("单次文件变更数量超过限制")

        paths: set[str] = set()
        total_chars = 0
        for change in plan.changes:
            if not change.path or not change.reason.strip():
                raise SchemaValidationError("文件变更缺少 path 或 reason")
            path = PurePosixPath(change.path)
            if self.PROTECTED_PARTS.intersection(path.parts):
                raise SchemaValidationError(f"禁止修改受保护路径: {change.path}")
            if "\x00" in change.content:
                raise SchemaValidationError(f"文件内容包含空字节: {change.path}")
            if len(change.reason) > 1000:
                raise SchemaValidationError(f"修改原因过长: {change.path}")
            if change.path in paths:
                raise SchemaValidationError(f"重复的文件路径: {change.path}")
            paths.add(change.path)
            total_chars += len(change.content)
            if not any(fnmatch(change.path, pattern) for pattern in task.allowed_paths):
                raise SchemaValidationError(f"路径不在允许范围: {change.path}")
        if total_chars > self.max_content_chars:
            raise SchemaValidationError("单次文件内容总量超过限制")
