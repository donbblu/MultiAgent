from __future__ import annotations

import json

from .harness import GraphValidationError, TaskGraph, TaskSpec
from .model import ModelClient, ModelError
from .models import TaskContext


class StructuredTaskPlanner:
    """将需求拆为受 Schema 和 Harness 双重约束的任务图。"""

    def __init__(self, client: ModelClient, max_repairs: int = 1) -> None:
        self.client = client
        self.max_repairs = max_repairs

    def create_graph(self, task: TaskContext) -> TaskGraph:
        error = ""
        for attempt in range(self.max_repairs + 1):
            data = self.client.generate_json([
                {"role": "system", "content": (
                    "你是 Coding Harness 的 Planner，只输出 JSON。将需求拆成少量可独立验收的实现任务。"
                    "任务之间通过 dependencies 和 Artifact 交接；能并行的任务不得制造虚假依赖。"
                    "当前执行器支持 implementer 角色，每个任务必须输出一个唯一 patch Artifact。"
                    "write_scopes 应尽量互斥；禁止 .env、.git、.runs 和绝对路径。"
                    "格式：{\"tasks\":[{\"task_id\":\"core\",\"title\":\"...\","
                    "\"objective\":\"...\",\"role\":\"implementer\",\"dependencies\":[],"
                    "\"acceptance_criteria\":[\"...\"],\"read_scopes\":[\"...\"],"
                    "\"write_scopes\":[\"app.py\"],\"input_artifacts\":[],"
                    "\"output_artifacts\":[\"core-patch\"],\"context_queries\":[]}]}."
                )},
                {"role": "user", "content": json.dumps({
                    "task": task.model_input(), "previous_validation_error": error,
                    "repair_attempt": attempt,
                }, ensure_ascii=False)},
            ])
            try:
                raw_tasks = data["tasks"]
                if not isinstance(raw_tasks, list):
                    raise TypeError("tasks 必须是数组")
                specs = tuple(self._parse(item) for item in raw_tasks)
                return TaskGraph(specs)
            except (KeyError, TypeError, GraphValidationError, ValueError) as exc:
                error = str(exc)
        raise ModelError(f"Planner 无法生成合法任务图: {error}")

    @staticmethod
    def _strings(item: dict[str, object], key: str) -> tuple[str, ...]:
        value = item.get(key, [])
        if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
            raise TypeError(f"{key} 必须是字符串数组")
        return tuple(value)

    @classmethod
    def _parse(cls, item: object) -> TaskSpec:
        if not isinstance(item, dict):
            raise TypeError("每个 task 必须是对象")
        required = ("task_id", "title", "objective", "role")
        if not all(isinstance(item.get(key), str) for key in required):
            raise TypeError("task_id/title/objective/role 必须是字符串")
        return TaskSpec(
            task_id=str(item["task_id"]), title=str(item["title"]),
            objective=str(item["objective"]), role=str(item["role"]),
            dependencies=cls._strings(item, "dependencies"),
            acceptance_criteria=cls._strings(item, "acceptance_criteria"),
            read_scopes=cls._strings(item, "read_scopes"),
            write_scopes=cls._strings(item, "write_scopes"),
            input_artifacts=cls._strings(item, "input_artifacts"),
            output_artifacts=cls._strings(item, "output_artifacts"),
            context_queries=cls._strings(item, "context_queries"),
            required_verified_inputs=cls._strings(
                item, "required_verified_inputs"
            ),
            required_capabilities=cls._strings(
                item, "required_capabilities"
            ),
            input_protocols=cls._strings(item, "input_protocols"),
            output_protocols=cls._strings(item, "output_protocols"),
            required_policy_tags=cls._strings(
                item, "required_policy_tags"
            ),
            independent_from_tasks=cls._strings(
                item, "independent_from_tasks"
            ),
        )
