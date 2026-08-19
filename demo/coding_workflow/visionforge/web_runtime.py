from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

from ..artifacts import Artifact, ArtifactStore
from ..integration import PatchIntegrator
from ..model import ModelCapability, ModelClientFactory, load_env_file
from ..models import ImplementationPlan
from ..workspace import ProjectWorkspace
from .agents import (
    RequirementAnalyst,
    VisionForgeDeveloper,
    VisionForgeFixer,
    VisualReviewer,
)
from .assets import ImageArtifactRef, ImageAssetError, ImageAssetStore
from .browser import (
    BrowserProcessRunner,
    BrowserProjectConfig,
    PlaywrightBrowserTester,
)
from .scenario import VisionForgeScenarioRunner
from .runner import VisionForgeRunResult


class VisionForgeTaskExecutor(Protocol):
    def __call__(
        self,
        task_id: str,
        requirement: str,
        reference_image_artifact_ref: str,
        task_root: Path,
        project_root: Path,
        artifacts: ArtifactStore,
        image_assets: ImageAssetStore,
    ) -> VisionForgeRunResult: ...


class VisionForgeWebError(ValueError):
    pass


@dataclass(frozen=True)
class UploadedImage:
    image: ImageArtifactRef
    created_at: str


class VisionForgeWebRuntime:
    """Web/API 与确定性 VisionForge Runtime 之间的窄适配层。"""

    ALLOWED_UPLOAD_TYPES = frozenset({"image/png", "image/jpeg"})
    REQUIRED_TEXT_MODEL_CAPABILITIES = frozenset({
        ModelCapability.TEXT,
        ModelCapability.TOOL_CALLING,
        ModelCapability.STRUCTURED_OUTPUT,
    })
    REQUIRED_VISION_MODEL_CAPABILITIES = frozenset({
        ModelCapability.TEXT,
        ModelCapability.VISION,
        ModelCapability.STRUCTURED_OUTPUT,
    })

    def __init__(
        self,
        runtime_root: Path,
        template_root: Path,
        *,
        executor: VisionForgeTaskExecutor | None = None,
        project_preparer: Callable[[Path], None] | None = None,
        env_file: Path | None = None,
    ) -> None:
        self.runtime_root = runtime_root.resolve()
        self.template_root = template_root.resolve()
        if not self.template_root.is_dir():
            raise ValueError("VisionForge Vue 模板不存在")
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.image_assets = ImageAssetStore(self.runtime_root / "assets")
        self.catalog_path = self.runtime_root / "web.sqlite3"
        with self._connect_catalog() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS uploaded_images (
                    asset_id TEXT PRIMARY KEY,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
        self.env_file = env_file
        self.executor = executor or self._execute_real
        self.project_preparer = project_preparer or self._prepare_project
        self._assets: dict[str, UploadedImage] = {}
        self._tasks: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()

    def upload_image(self, data: bytes, declared_mime_type: str) -> dict[str, object]:
        mime_type = declared_mime_type.split(";", 1)[0].strip().lower()
        if mime_type not in self.ALLOWED_UPLOAD_TYPES:
            raise VisionForgeWebError("只允许上传 PNG 或 JPEG 图片")
        try:
            image = self.image_assets.put(data)
        except ImageAssetError as exc:
            raise VisionForgeWebError(str(exc)) from exc
        if image.mime_type != mime_type:
            raise VisionForgeWebError("请求 Content-Type 与图片真实格式不一致")
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._assets.setdefault(image.asset_id, UploadedImage(image, created_at))
            uploaded = self._assets[image.asset_id]
        with self._connect_catalog() as connection:
            connection.execute(
                """INSERT INTO uploaded_images(asset_id, metadata, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(asset_id) DO NOTHING""",
                (image.asset_id, json.dumps(image.to_dict()), uploaded.created_at),
            )
        return self._public_image(uploaded.image, uploaded.created_at)

    def asset(self, asset_id: str) -> UploadedImage:
        if not self._valid_asset_id(asset_id):
            raise KeyError("图片资产不存在")
        with self._lock:
            uploaded = self._assets.get(asset_id)
        if uploaded:
            return uploaded
        with self._connect_catalog() as connection:
            row = connection.execute(
                "SELECT metadata, created_at FROM uploaded_images WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise KeyError("图片资产不存在")
        try:
            uploaded = UploadedImage(
                ImageArtifactRef.from_dict(json.loads(row[0])), str(row[1])
            )
            self.image_assets.read(uploaded.image)
        except (ImageAssetError, ValueError, json.JSONDecodeError) as exc:
            raise KeyError("图片资产不存在或已损坏") from exc
        with self._lock:
            self._assets[asset_id] = uploaded
        return uploaded

    def read_asset(self, asset_id: str) -> tuple[bytes, str]:
        uploaded = self.asset(asset_id)
        return self.image_assets.read(uploaded.image), uploaded.image.mime_type

    def submit_task(self, requirement: str, asset_id: str) -> str:
        normalized = requirement.strip()
        if not 3 <= len(normalized) <= 4000:
            raise VisionForgeWebError("需求长度必须在 3 到 4000 字符之间")
        uploaded = self.asset(asset_id)
        task_id = f"VF-{uuid.uuid4().hex[:12]}"
        task_root = (self.runtime_root / "tasks" / task_id).resolve()
        expected_root = (self.runtime_root / "tasks").resolve()
        if not task_root.is_relative_to(expected_root):
            raise VisionForgeWebError("任务目录越过 Runtime 边界")
        artifacts = ArtifactStore()
        reference_ref = artifacts.put(Artifact.create(
            "reference-image",
            task_id,
            uploaded.image.to_dict(),
            kind="reference_image",
            metadata={
                "asset_uri": uploaded.image.uri,
                "mime_type": uploaded.image.mime_type,
                "sha256": uploaded.image.sha256,
            },
        ))
        task = {
            "id": task_id,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requirement": normalized,
            "asset_id": asset_id,
            "reference_image_artifact_ref": reference_ref,
            "result": None,
            "error": None,
            "_task_root": task_root,
            "_artifacts": artifacts,
            "_done": threading.Event(),
        }
        with self._lock:
            self._tasks[task_id] = task
        threading.Thread(
            target=self._run_task,
            args=(task_id,),
            daemon=True,
            name=f"visionforge-web-{task_id}",
        ).start()
        return task_id

    def wait(self, task_id: str, timeout: float = 10) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            done = task.get("_done") if task else None
        if not isinstance(done, threading.Event):
            raise KeyError("VisionForge 任务不存在")
        return done.wait(timeout)

    def task_snapshot(self, task_id: str) -> dict[str, object]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError("VisionForge 任务不存在")
            snapshot = {
                key: value for key, value in task.items() if not key.startswith("_")
            }
            artifacts = task["_artifacts"]
        assert isinstance(artifacts, ArtifactStore)
        uploaded = self.asset(str(snapshot["asset_id"]))
        snapshot["reference_image"] = self._public_image(
            uploaded.image, uploaded.created_at
        )
        snapshot["artifacts"] = [
            self._public_artifact(artifact, validation.state.value)
            for artifact, validation in artifacts.snapshot()
        ]
        return _jsonable(snapshot)

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
        task_root = task["_task_root"]
        artifacts = task["_artifacts"]
        assert isinstance(task_root, Path)
        assert isinstance(artifacts, ArtifactStore)
        project_root = task_root / "project"
        try:
            with self._run_lock:
                with self._lock:
                    task["status"] = "preparing"
                task_root.mkdir(parents=True, exist_ok=True)
                self.project_preparer(project_root)
                with self._lock:
                    task["status"] = "running"
                result = self.executor(
                    task_id,
                    str(task["requirement"]),
                    str(task["reference_image_artifact_ref"]),
                    task_root,
                    project_root,
                    artifacts,
                    self.image_assets,
                )
                with self._lock:
                    task["status"] = result.status
                    task["result"] = {
                        "status": result.status,
                        "fix_attempts": result.fix_attempts,
                        "changed_files": list(result.changed_files),
                        "browser_passed": result.browser_passed,
                        "visual_score": result.visual_score,
                        "run_artifact_ref": result.run_artifact_ref,
                        "quality_gate_artifact_ref": (
                            result.quality_gate_artifact_ref
                        ),
                        "cycles": [item.to_dict() for item in result.cycles],
                    }
        except Exception as exc:
            with self._lock:
                task["status"] = "failed"
                task["error"] = str(exc)
        finally:
            done = task.get("_done")
            if isinstance(done, threading.Event):
                done.set()

    def _execute_real(
        self,
        task_id: str,
        requirement: str,
        reference_image_artifact_ref: str,
        task_root: Path,
        project_root: Path,
        artifacts: ArtifactStore,
        image_assets: ImageAssetStore,
    ) -> VisionForgeRunResult:
        if self.env_file:
            load_env_file(self.env_file)
        text_client = ModelClientFactory.create(
            ModelClientFactory.config_from_env(),
            required_capabilities=self.REQUIRED_TEXT_MODEL_CAPABILITIES,
        )
        vision_client = ModelClientFactory.create(
            ModelClientFactory.vision_config_from_env(),
            required_capabilities=self.REQUIRED_VISION_MODEL_CAPABILITIES,
        )
        BrowserProjectConfig.load(project_root)
        raw_config = json.loads(
            (project_root / "visionforge.template.json").read_text(encoding="utf-8")
        )
        allowed_paths = raw_config.get("allowed_paths")
        if not isinstance(allowed_paths, list) or not all(
            isinstance(item, str) and item for item in allowed_paths
        ):
            raise VisionForgeWebError("Vue 模板 allowed_paths 无效")
        overrides = {
            name: value
            for name, value in {
                "node": os.environ.get("VISIONFORGE_NODE", ""),
                "pnpm": os.environ.get("VISIONFORGE_PNPM", ""),
            }.items()
            if value
        }
        environment: dict[str, str] = {}
        node = overrides.get("node")
        if node:
            environment["PATH"] = f"{Path(node).parent}:/usr/bin:/bin"
        browser = os.environ.get("VISIONFORGE_BROWSER_EXECUTABLE", "").strip()
        if browser:
            environment["VISIONFORGE_BROWSER_EXECUTABLE"] = browser
        process_runner = BrowserProcessRunner(
            executable_overrides=overrides,
            environment=environment,
        )
        workspace = ProjectWorkspace(project_root)
        return VisionForgeScenarioRunner(
            artifacts=artifacts,
            workspace=workspace,
            integrator=PatchIntegrator(workspace, allowed_paths),
            analyst=RequirementAnalyst(vision_client, artifacts, image_assets),
            developer=VisionForgeDeveloper(text_client, artifacts),
            browser_tester=PlaywrightBrowserTester(
                project_root,
                process_runner,
                artifacts,
                image_assets,
                task_root / "browser-runtime",
            ),
            visual_reviewer=VisualReviewer(
                vision_client, artifacts, image_assets
            ),
            fixer=VisionForgeFixer(text_client, artifacts),
            max_fix_attempts=2,
            runtime_path=task_root / "visionforge-scenario.sqlite3",
        ).run(
            task_id=task_id,
            requirement=requirement,
            reference_image_artifact_ref=reference_image_artifact_ref,
            run_id=f"visionforge-web:{task_id}",
        )

    def _prepare_project(self, destination: Path) -> None:
        if destination.exists():
            raise VisionForgeWebError("VisionForge 任务项目目录已存在")
        shutil.copytree(
            self.template_root,
            destination,
            symlinks=True,
            copy_function=self._link_or_copy,
        )

    @staticmethod
    def _link_or_copy(source: str, destination: str) -> str:
        try:
            os.link(source, destination)
            return destination
        except OSError:
            return shutil.copy2(source, destination)

    def _connect_catalog(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.catalog_path), timeout=5)

    def _public_image(
        self, image: ImageArtifactRef, created_at: str
    ) -> dict[str, object]:
        return {
            "asset_id": image.asset_id,
            "uri": image.uri,
            "mime_type": image.mime_type,
            "size_bytes": image.size_bytes,
            "width": image.width,
            "height": image.height,
            "created_at": created_at,
            "url": f"/api/visionforge/assets/{image.asset_id}",
        }

    def _public_artifact(
        self, artifact: Artifact, validation_state: str
    ) -> dict[str, object]:
        content: object
        if isinstance(artifact.content, ImplementationPlan):
            content = {
                "summary": artifact.content.summary,
                "changes": [
                    {
                        "path": item.path,
                        "content": item.content,
                        "reason": item.reason,
                    }
                    for item in artifact.content.changes
                ],
                "suggested_checks": artifact.content.suggested_checks,
            }
        elif artifact.kind in {"reference_image", "actual_screenshot"}:
            image = ImageArtifactRef.from_dict(artifact.content)
            content = self._public_image(image, artifact.created_at)
        elif artifact.kind == "build_result" and isinstance(artifact.content, dict):
            content = {
                key: artifact.content.get(key)
                for key in ("command", "exit_code", "duration_ms", "timed_out", "passed")
            }
        else:
            content = artifact.content
        return {
            "ref": f"artifact://{artifact.artifact_id}",
            "name": artifact.name,
            "kind": artifact.kind,
            "content": _jsonable(content),
            "metadata": _jsonable(dict(artifact.metadata)),
            "validation_state": validation_state,
            "created_at": artifact.created_at,
        }

    @staticmethod
    def _valid_asset_id(value: str) -> bool:
        return len(value) == 64 and all(
            char in "0123456789abcdef" for char in value
        )


def _jsonable(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
