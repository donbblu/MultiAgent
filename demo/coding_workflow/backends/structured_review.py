from __future__ import annotations

import json

from ..memory import RoleMemoryView
from ..model import ModelClient, ModelError
from ..models import ReviewFinding, ReviewResult


class StructuredReviewBackend:
    """供应商无关的只读代码审查 Backend。"""

    ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}

    def __init__(self, client: ModelClient) -> None:
        self.client = client

    def review(self, memory: RoleMemoryView) -> ReviewResult:
        system = (
            "你是独立 Reviewer，只审查需求覆盖、正确性、安全性和可维护性。"
            "不得修改文件、执行命令或批准自己实施的变更。只输出 JSON。"
            "仅报告会影响验收或造成明确风险的问题，避免风格偏好。"
            '格式：{"passed":true,"summary":"...","findings":'
            '[{"severity":"low|medium|high|critical","path":"...","message":"..."}]}'
        )
        data = self.client.generate_json(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": memory.model_input(),
                            "project_files": [
                                {
                                    "path": item.path,
                                    "content": item.content,
                                    "truncated": item.truncated,
                                }
                                for item in memory.project_files
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
        try:
            passed = data["passed"]
            summary = data["summary"]
            raw_findings = data.get("findings", [])
            if not isinstance(passed, bool) or not isinstance(summary, str):
                raise TypeError("passed 必须是布尔值，summary 必须是字符串")
            if not isinstance(raw_findings, list):
                raise TypeError("findings 必须是数组")
            findings: list[ReviewFinding] = []
            for item in raw_findings:
                if not isinstance(item, dict):
                    raise TypeError("finding 必须是对象")
                severity = item.get("severity")
                message = item.get("message")
                path = item.get("path", "")
                if (
                    severity not in self.ALLOWED_SEVERITIES
                    or not isinstance(message, str)
                    or not isinstance(path, str)
                ):
                    raise TypeError("finding 字段不合法")
                findings.append(ReviewFinding(severity, message, path))
            blocking = [item for item in findings if item.severity in {"high", "critical"}]
            effective_passed = passed and not blocking
            feedback = [
                f"审查 {item.severity}: {item.path or '项目'}: {item.message}"
                for item in findings
                if item.severity in {"medium", "high", "critical"}
            ]
            return ReviewResult(effective_passed, summary, findings, feedback)
        except (KeyError, TypeError) as exc:
            raise ModelError(f"模型输出不符合 ReviewResult: {exc}") from exc
