from __future__ import annotations

from dataclasses import dataclass

from ..artifacts import Artifact, ArtifactStore
from .artifact_types import BROWSER_RUN, QUALITY_GATE, VISUAL_REVIEW
from .contracts import BrowserRunResult, VisualReview


class QualityGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualityGateDecision:
    passed: bool
    build_passed: bool
    assertions_passed: bool
    console_passed: bool
    page_errors_passed: bool
    network_passed: bool
    browser_run_passed: bool
    visual_model_passed: bool
    visual_score_passed: bool
    no_blocking_issues: bool
    failures: tuple[str, ...]
    artifact_ref: str


class VisionForgeQualityGate:
    """只根据 Runtime 证据判定；模型的 passed 只是其中一个输入。"""

    def __init__(
        self,
        artifacts: ArtifactStore,
        *,
        minimum_visual_score: float = 85,
    ) -> None:
        if not 0 <= minimum_visual_score <= 100:
            raise ValueError("minimum_visual_score 必须在 0 到 100 之间")
        self.artifacts = artifacts
        self.minimum_visual_score = minimum_visual_score

    def evaluate(
        self,
        *,
        task_id: str,
        round_index: int,
        build_artifact_ref: str,
        browser_run_artifact_ref: str,
        visual_review_artifact_ref: str,
    ) -> QualityGateDecision:
        build_artifact = self._artifact(
            build_artifact_ref, task_id, "build_result"
        )
        browser_artifact = self._artifact(
            browser_run_artifact_ref, task_id, BROWSER_RUN
        )
        visual_artifact = self._artifact(
            visual_review_artifact_ref, task_id, VISUAL_REVIEW
        )
        if not isinstance(build_artifact.content, dict):
            raise QualityGateError("Build Result Artifact 内容必须是对象")
        if not isinstance(browser_artifact.content, dict):
            raise QualityGateError("Browser Run Artifact 内容必须是对象")
        screenshot_ref = browser_artifact.content.get("screenshot_artifact_ref")
        if not isinstance(screenshot_ref, str):
            raise QualityGateError("Browser Run 缺少截图 Artifact 引用")
        browser = BrowserRunResult.from_runner_payload(
            browser_artifact.content, screenshot_ref
        )
        visual = VisualReview.from_dict(visual_artifact.content)

        build_passed = build_artifact.content.get("passed") is True
        assertions_passed = all(item.passed for item in browser.assertions)
        console_passed = not any(
            item.level == "error" for item in browser.console_messages
        )
        page_errors_passed = not browser.page_errors
        network_passed = not browser.network_errors
        visual_model_passed = visual.passed
        visual_score_passed = visual.score >= self.minimum_visual_score
        no_blocking_issues = not visual.blocking_issues
        checks = (
            (build_passed, "项目构建未通过"),
            (assertions_passed, "DOM 或交互断言未通过"),
            (console_passed, "浏览器存在严重控制台错误"),
            (page_errors_passed, "浏览器存在页面运行错误"),
            (network_passed, "浏览器存在被阻止的外部网络请求"),
            (browser.passed, "Browser Run 协议判定未通过"),
            (visual_model_passed, "Visual Reviewer 未声明通过"),
            (visual_score_passed, "视觉评分低于 Runtime 阈值"),
            (no_blocking_issues, "仍存在 P1/P2 视觉问题"),
        )
        failures = tuple(message for passed, message in checks if not passed)
        passed = not failures
        content = {
            "schema_version": "1.0",
            "round_index": round_index,
            "passed": passed,
            "checks": {
                "build_passed": build_passed,
                "assertions_passed": assertions_passed,
                "console_passed": console_passed,
                "page_errors_passed": page_errors_passed,
                "network_passed": network_passed,
                "browser_run_passed": browser.passed,
                "visual_model_passed": visual_model_passed,
                "visual_score_passed": visual_score_passed,
                "no_blocking_issues": no_blocking_issues,
            },
            "visual_score": visual.score,
            "minimum_visual_score": self.minimum_visual_score,
            "blocking_issue_count": len(visual.blocking_issues),
            "failures": list(failures),
            "evidence": {
                "build_artifact_ref": build_artifact_ref,
                "browser_run_artifact_ref": browser_run_artifact_ref,
                "visual_review_artifact_ref": visual_review_artifact_ref,
                "screenshot_artifact_ref": screenshot_ref,
            },
        }
        artifact_ref = self.artifacts.put(Artifact.create(
            f"visionforge-quality-gate-{round_index}",
            task_id,
            content,
            kind=QUALITY_GATE,
            metadata={"round_index": round_index, "passed": passed},
        ))
        return QualityGateDecision(
            passed,
            build_passed,
            assertions_passed,
            console_passed,
            page_errors_passed,
            network_passed,
            browser.passed,
            visual_model_passed,
            visual_score_passed,
            no_blocking_issues,
            failures,
            artifact_ref,
        )

    def _artifact(self, reference: str, task_id: str, kind: str) -> Artifact:
        artifact = self.artifacts.get(reference)
        if artifact.task_id != task_id or artifact.kind != kind:
            raise QualityGateError(
                f"Artifact {reference} 必须属于任务 {task_id} 且类型为 {kind}"
            )
        return artifact
