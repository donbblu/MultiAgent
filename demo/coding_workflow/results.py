from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import uuid4


T = TypeVar("T")


class StaleResultError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResultEnvelope(Generic[T]):
    result_id: str
    task_id: str
    task_version: int
    producer_role: str
    result_type: str
    payload: T

    @classmethod
    def create(
        cls,
        task_id: str,
        task_version: int,
        producer_role: str,
        result_type: str,
        payload: T,
    ) -> "ResultEnvelope[T]":
        return cls(
            result_id=uuid4().hex,
            task_id=task_id,
            task_version=task_version,
            producer_role=producer_role,
            result_type=result_type,
            payload=payload,
        )

    def validate_for(self, task_id: str, task_version: int) -> None:
        if self.task_id != task_id:
            raise StaleResultError("结果属于其他任务")
        if self.task_version != task_version:
            raise StaleResultError(
                f"结果版本 {self.task_version} 与任务版本 {task_version} 不一致"
            )
