from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class RunRecorder:
    """以 JSONL 追加记录事件，同时保存最新任务快照。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def record(self, task_id: str, event: str, payload: Any) -> None:
        task_dir = self.root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "payload": _jsonable(payload),
        }
        with (task_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def snapshot(self, task: Any) -> None:
        task_dir = self.root / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task.json").write_text(
            json.dumps(_jsonable(task), ensure_ascii=False, indent=2), encoding="utf-8"
        )
