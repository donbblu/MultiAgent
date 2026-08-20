from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from .base import (
    ImageContentPart,
    ModelClient,
    ModelRequest,
    ModelResponse,
    TextContentPart,
)


class ModelBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelBudgetSnapshot:
    max_model_calls: int
    max_total_tokens: int
    max_tokens_per_call: int
    attempted_calls: int
    completed_calls: int
    failed_calls: int
    observed_tokens: int
    accounted_tokens: int
    inflight_reserved_tokens: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_total_tokens": self.max_total_tokens,
            "max_tokens_per_call": self.max_tokens_per_call,
            "attempted_calls": self.attempted_calls,
            "completed_calls": self.completed_calls,
            "failed_calls": self.failed_calls,
            "observed_tokens": self.observed_tokens,
            "accounted_tokens": self.accounted_tokens,
            "inflight_reserved_tokens": self.inflight_reserved_tokens,
        }


class ModelCallBudget:
    """跨 Worker 的共享停止条件；每次调用前预留最坏单次 Token。"""

    def __init__(
        self,
        *,
        max_model_calls: int,
        max_total_tokens: int,
        max_tokens_per_call: int,
    ) -> None:
        if min(max_model_calls, max_total_tokens, max_tokens_per_call) <= 0:
            raise ValueError("模型调用和 Token 上限必须大于 0")
        if max_tokens_per_call > max_total_tokens:
            raise ValueError("单次 Token 上限不能超过全局 Token 上限")
        self.max_model_calls = max_model_calls
        self.max_total_tokens = max_total_tokens
        self.max_tokens_per_call = max_tokens_per_call
        self._attempted_calls = 0
        self._completed_calls = 0
        self._failed_calls = 0
        self._observed_tokens = 0
        self._accounted_tokens = 0
        self._inflight_reserved_tokens = 0
        self._lock = threading.Lock()

    def before_call(self, request_upper_bound: int) -> int:
        if request_upper_bound <= 0:
            raise ValueError("请求 Token 上界必须大于 0")
        if request_upper_bound > self.max_tokens_per_call:
            raise ModelBudgetExceeded(
                "模型请求最坏用量超过单次 Token 上限"
            )
        reservation = self.max_tokens_per_call
        with self._lock:
            if self._attempted_calls >= self.max_model_calls:
                raise ModelBudgetExceeded("已达到模型调用次数硬上限")
            if (
                self._accounted_tokens
                + self._inflight_reserved_tokens
                + reservation
                > self.max_total_tokens
            ):
                raise ModelBudgetExceeded("剩余 Token 不足以预留下一次调用")
            self._attempted_calls += 1
            self._inflight_reserved_tokens += reservation
        return reservation

    def after_call(self, response: ModelResponse, reservation: int) -> None:
        observed = max(
            response.usage.total_tokens,
            response.usage.input_tokens + response.usage.output_tokens,
        )
        accounted = observed if observed > 0 else reservation
        with self._lock:
            self._inflight_reserved_tokens -= reservation
            self._completed_calls += 1
            self._observed_tokens += observed
            self._accounted_tokens += accounted
            if accounted > reservation:
                raise ModelBudgetExceeded(
                    "供应商报告用量超过调用前预留，停止后续调用"
                )

    def after_error(self, reservation: int) -> None:
        with self._lock:
            self._inflight_reserved_tokens -= reservation
            self._failed_calls += 1
            # A failed HTTP/parse path may still have consumed provider Tokens.
            # Without authoritative usage, charge the full reservation.
            self._accounted_tokens += reservation

    def snapshot(self) -> ModelBudgetSnapshot:
        with self._lock:
            return ModelBudgetSnapshot(
                self.max_model_calls,
                self.max_total_tokens,
                self.max_tokens_per_call,
                self._attempted_calls,
                self._completed_calls,
                self._failed_calls,
                self._observed_tokens,
                self._accounted_tokens,
                self._inflight_reserved_tokens,
            )


def conservative_request_token_upper_bound(
    request: ModelRequest, *, max_output_tokens: int
) -> int:
    """以序列化字节数作为输入 Token 保守上界，并加协议余量。"""
    if max_output_tokens <= 0:
        raise ValueError("max_output_tokens 必须大于 0")
    messages: list[dict[str, object]] = []
    for message in request.messages:
        parts: list[dict[str, object]] = []
        for part in message.content:
            if isinstance(part, TextContentPart):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, ImageContentPart):
                parts.append({
                    "type": "image",
                    "mime_type": part.mime_type,
                    "encoded_bytes_upper_bound": ((len(part.data) + 2) // 3) * 4,
                })
        messages.append({"role": message.role, "content": parts})
    serialized = json.dumps(
        {
            "messages": messages,
            "response_schema": dict(request.response_schema),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    # JSON-object providers may receive the schema as an additional system
    # message. Doubling the serialized bytes plus fixed protocol overhead is a
    # conservative tokenizer-independent reservation for this text workflow.
    return len(serialized) * 2 + 4_096 + max_output_tokens


class BudgetedModelClient:
    def __init__(
        self,
        client: ModelClient,
        budget: ModelCallBudget,
        *,
        max_output_tokens: int,
    ) -> None:
        self.client = client
        self.budget = budget
        self.max_output_tokens = max_output_tokens

    @property
    def capabilities(self):
        return self.client.capabilities

    def generate_structured(self, request: ModelRequest) -> ModelResponse:
        upper_bound = conservative_request_token_upper_bound(
            request, max_output_tokens=self.max_output_tokens
        )
        reservation = self.budget.before_call(upper_bound)
        try:
            response = self.client.generate_structured(request)
        except Exception:
            self.budget.after_error(reservation)
            raise
        self.budget.after_call(response, reservation)
        return response

    def generate_json(self, messages: list[dict[str, str]]) -> dict[str, object]:
        request = ModelRequest.from_text_messages(messages)
        return dict(self.generate_structured(request).data)
