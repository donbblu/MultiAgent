from __future__ import annotations

from typing import Any, Protocol


class ModelError(RuntimeError):
    pass


class ModelClient(Protocol):
    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, Any]: ...
