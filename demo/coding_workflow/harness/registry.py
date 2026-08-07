from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, TypeVar


Worker = TypeVar("Worker")


class WorkerRegistry:
    """将角色解析为 Worker；Harness 不再持有固定 Agent 拓扑。"""

    def __init__(self) -> None:
        self._workers: dict[str, object] = {}

    def register(self, role: str, worker: object, *, replace: bool = False) -> None:
        if not role.strip() or worker is None:
            raise ValueError("role 和 worker 不能为空")
        if role in self._workers and not replace:
            raise ValueError(f"角色已注册 Worker: {role}")
        self._workers[role] = worker

    def resolve(self, role: str, *, required: bool = True) -> object | None:
        worker = self._workers.get(role)
        if worker is None and required:
            raise KeyError(f"角色没有可用 Worker: {role}")
        return worker

    def snapshot(self) -> Mapping[str, object]:
        return MappingProxyType(dict(self._workers))
