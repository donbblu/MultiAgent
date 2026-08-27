from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Callable, Mapping

from .artifacts import ArtifactStore
from .command_validators import register_core_command_validators
from .local_execution_approval import LocalExecutionApprover
from .requirements import AcceptanceCriterion, ValidatorProfile, ValidatorSpec
from .validator_runtime import (
    ProfileVerificationResult,
    ValidatorProfileRunner,
    ValidatorRegistry,
)
from .workspace import ProjectWorkspace


_HIDDEN_DIRECTORY = ".harness-hidden-tests"
_TASK_ID = re.compile(r"^[a-z][a-z0-9-]*$")


def _relative_path(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"{field_name} 必须是安全相对路径")
    if path.parts[0] in {".git", _HIDDEN_DIRECTORY}:
        raise ValueError(f"{field_name} 使用了保留路径")
    return str(path)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _empty_target(path: Path) -> Path:
    target = path.resolve()
    if target.exists() and not target.is_dir():
        raise ValueError(f"目标必须是目录: {target}")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"目标目录必须为空: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


@dataclass(frozen=True)
class FixedFile:
    path: str
    sha256: str

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> "FixedFile":
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} 必须是对象")
        if set(value) != {"path", "sha256"}:
            raise ValueError(f"{field_name} 字段无效")
        relative = _relative_path(value["path"], f"{field_name}.path")
        digest = value["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest.lower()
        ):
            raise ValueError(f"{field_name}.sha256 必须是 SHA-256")
        return cls(relative, digest.lower())


@dataclass(frozen=True)
class FixedCodingTask:
    task_id: str
    objective: str
    task_root: Path
    starter_files: tuple[FixedFile, ...]
    hidden_files: tuple[FixedFile, ...]
    solution_files: tuple[FixedFile, ...]
    allowed_write_paths: tuple[str, ...]
    validator_configs: Mapping[str, Mapping[str, object]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validator_configs",
            MappingProxyType({
                kind: MappingProxyType(dict(config))
                for kind, config in self.validator_configs.items()
            }),
        )

    def prepare_workspace(self, target: Path) -> Path:
        """只复制公开 starter；隐藏测试和参考答案不会进入 Agent 工作区。"""
        workspace = _empty_target(target)
        self._copy_group("starter", self.starter_files, workspace)
        return workspace

    def apply_reference_solution(self, workspace: Path) -> None:
        """仅供离线 Harness 自测；真实 Agent 运行不得调用。"""
        root = workspace.resolve()
        if not root.is_dir():
            raise ValueError("Agent Workspace 不存在")
        for item in self.solution_files:
            if item.path not in self.allowed_write_paths:
                raise PermissionError(f"参考答案越过写入范围: {item.path}")
        self._copy_group("solution", self.solution_files, root)

    def prepare_validation_workspace(
        self, workspace: Path, target: Path
    ) -> Path:
        """创建 Runtime 私有副本，再注入隐藏验收。"""
        source = workspace.resolve()
        if not source.is_dir():
            raise ValueError("Agent Workspace 不存在")
        self.assert_candidate_scope(source)
        requested = target.resolve()
        if requested.is_relative_to(source) or source.is_relative_to(requested):
            raise ValueError("验证 Workspace 必须与 Agent Workspace 隔离")
        validation = _empty_target(target)
        for candidate in sorted(source.rglob("*")):
            relative = candidate.relative_to(source)
            if candidate.is_symlink():
                raise ValueError(f"Workspace 不允许符号链接: {relative}")
            if not candidate.is_file():
                continue
            parts = relative.parts
            if ".git" in parts or _HIDDEN_DIRECTORY in parts:
                raise PermissionError("Agent Workspace 使用了 Runtime 保留目录")
            if "__pycache__" in parts or candidate.suffix in {".pyc", ".pyo"}:
                continue
            destination = validation / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, destination)
        self._copy_group(
            "hidden", self.hidden_files, validation / _HIDDEN_DIRECTORY
        )
        return validation

    def assert_candidate_scope(self, workspace: Path) -> None:
        """拒绝修改公开测试、删除受保护文件或创建未授权文件。"""
        root = workspace.resolve()
        starter = {item.path: item.sha256 for item in self.starter_files}
        allowed = set(self.allowed_write_paths)
        actual: dict[str, Path] = {}
        for candidate in sorted(root.rglob("*")):
            relative = candidate.relative_to(root)
            if candidate.is_symlink():
                raise ValueError(f"Workspace 不允许符号链接: {relative}")
            if not candidate.is_file():
                continue
            if "__pycache__" in relative.parts or candidate.suffix in {
                ".pyc", ".pyo"
            }:
                continue
            path = str(PurePosixPath(*relative.parts))
            if path.startswith(f"{_HIDDEN_DIRECTORY}/"):
                raise PermissionError("Agent Workspace 使用了 Runtime 保留目录")
            actual[path] = candidate
        unauthorized = set(actual) - set(starter) - allowed
        if unauthorized:
            raise PermissionError(
                f"候选仓库包含未授权文件: {sorted(unauthorized)}"
            )
        protected_missing = set(starter) - set(actual) - allowed
        if protected_missing:
            raise PermissionError(
                f"候选仓库删除了受保护文件: {sorted(protected_missing)}"
            )
        protected_changed = {
            path for path, digest in starter.items()
            if path not in allowed
            and path in actual
            and _digest(actual[path]) != digest
        }
        if protected_changed:
            raise PermissionError(
                f"候选仓库修改了受保护文件: {sorted(protected_changed)}"
            )

    def acceptance_contract(
        self,
    ) -> tuple[tuple[AcceptanceCriterion, ...], ValidatorProfile]:
        criteria: list[AcceptanceCriterion] = []
        specs: list[ValidatorSpec] = []
        for kind, config in sorted(self.validator_configs.items()):
            suffix = kind.split(":", 1)[1]
            criterion = AcceptanceCriterion(
                f"{suffix}_passes",
                f"固定 {suffix} 验证必须通过",
                kind,
                {"outcome": "passed"},
            )
            criteria.append(criterion)
            specs.append(ValidatorSpec(
                f"{suffix}_validator",
                kind,
                (criterion.criterion_id,),
                config,
                required=True,
                bind_workspace=True,
            ))
        criterion_tuple = tuple(criteria)
        profile = ValidatorProfile(
            f"{self.task_id.replace('-', '_')}_profile",
            tuple(specs),
            {item.criterion_id: item.digest for item in criterion_tuple},
        )
        profile.validate_criteria(criterion_tuple)
        return criterion_tuple, profile

    def validator_registry(
        self,
        validation_workspace: Path,
        *,
        approver_factory: Callable[[], LocalExecutionApprover] | None = None,
    ) -> ValidatorRegistry:
        commands = {
            kind: tuple(
                tuple(command["argv"])
                for command in config["commands"]
            )
            for kind, config in self.validator_configs.items()
        }
        registry = ValidatorRegistry()
        register_core_command_validators(
            registry,
            validation_workspace,
            commands,
            max_timeout_seconds=10,
            approver_factory=approver_factory,
        )
        return registry

    def validate_candidate(
        self,
        *,
        workspace: Path,
        validation_workspace: Path,
        artifacts: ArtifactStore,
        subject_refs: tuple[str, ...],
        task_id: str,
        approver_factory: Callable[[], LocalExecutionApprover] | None = None,
    ) -> ProfileVerificationResult:
        private_workspace = self.prepare_validation_workspace(
            workspace, validation_workspace
        )
        _, profile = self.acceptance_contract()
        return ValidatorProfileRunner(
            profile,
            self.validator_registry(
                private_workspace,
                approver_factory=approver_factory,
            ),
            artifacts,
        ).run(
            task_id=task_id,
            subject_refs=subject_refs,
            workspace_hashes=ProjectWorkspace(
                private_workspace
            ).content_hashes(),
        )

    def _copy_group(
        self, group: str, files: tuple[FixedFile, ...], target: Path
    ) -> None:
        source_root = self.task_root / group
        for item in files:
            source = source_root / item.path
            destination = target / item.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


@dataclass(frozen=True)
class FixedCodingSuite:
    suite_id: str
    schema_version: str
    root: Path
    tasks: tuple[FixedCodingTask, ...]
    manifest_sha256: str

    @classmethod
    def load(cls, root: Path) -> "FixedCodingSuite":
        suite_root = root.resolve()
        manifest_path = suite_root / "suite.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取固定任务清单: {exc}") from exc
        if not isinstance(raw, Mapping) or set(raw) != {
            "schema_version", "suite_id", "tasks"
        }:
            raise ValueError("固定任务清单顶层字段无效")
        if raw["schema_version"] != "1.0":
            raise ValueError("只支持固定任务清单 1.0")
        if not isinstance(raw["suite_id"], str) or not raw["suite_id"].strip():
            raise ValueError("suite_id 不能为空")
        raw_tasks = raw["tasks"]
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ValueError("tasks 必须是非空数组")
        tasks = tuple(cls._task(suite_root, item) for item in raw_tasks)
        ids = tuple(item.task_id for item in tasks)
        if len(ids) != len(set(ids)):
            raise ValueError("固定任务 ID 不能重复")
        return cls(
            raw["suite_id"],
            raw["schema_version"],
            suite_root,
            tasks,
            _digest(manifest_path),
        )

    def task(self, task_id: str) -> FixedCodingTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"固定任务不存在: {task_id}")

    @classmethod
    def _task(cls, suite_root: Path, value: object) -> FixedCodingTask:
        if not isinstance(value, Mapping):
            raise ValueError("task 必须是对象")
        required = {
            "task_id", "objective", "path", "starter_files", "hidden_files",
            "solution_files", "allowed_write_paths", "validators",
        }
        if set(value) != required:
            raise ValueError("task 字段无效")
        task_id = value["task_id"]
        objective = value["objective"]
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError("task_id 格式无效")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective 不能为空")
        task_path = _relative_path(value["path"], "task.path")
        task_root = (suite_root / task_path).resolve()
        if not task_root.is_relative_to(suite_root) or not task_root.is_dir():
            raise ValueError("固定任务目录不存在或越界")

        def files(name: str) -> tuple[FixedFile, ...]:
            raw_files = value[name]
            if not isinstance(raw_files, list) or not raw_files:
                raise ValueError(f"{name} 必须是非空数组")
            parsed = tuple(
                FixedFile.from_dict(item, f"{name}[{index}]")
                for index, item in enumerate(raw_files)
            )
            if len({item.path for item in parsed}) != len(parsed):
                raise ValueError(f"{name} 路径不能重复")
            cls._verify_group(task_root, name.removesuffix("_files"), parsed)
            return parsed

        starter = files("starter_files")
        hidden = files("hidden_files")
        solution = files("solution_files")
        writes = value["allowed_write_paths"]
        if not isinstance(writes, list) or not writes:
            raise ValueError("allowed_write_paths 必须是非空数组")
        allowed = tuple(
            _relative_path(item, "allowed_write_paths") for item in writes
        )
        if len(allowed) != len(set(allowed)):
            raise ValueError("allowed_write_paths 不能重复")
        solution_paths = {item.path for item in solution}
        if not solution_paths.issubset(set(allowed)):
            raise ValueError("参考答案包含未授权写入路径")
        validators = cls._validators(value["validators"])
        return FixedCodingTask(
            task_id,
            objective,
            task_root,
            starter,
            hidden,
            solution,
            allowed,
            validators,
        )

    @staticmethod
    def _verify_group(
        task_root: Path, group: str, expected: tuple[FixedFile, ...]
    ) -> None:
        root = task_root / group
        if not root.is_dir():
            raise ValueError(f"固定任务缺少目录: {group}")
        actual: dict[str, Path] = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"固定任务不允许符号链接: {path}")
            if path.is_file():
                actual[str(path.relative_to(root))] = path
        if set(actual) != {item.path for item in expected}:
            raise ValueError(f"{group} 文件与清单不一致")
        for item in expected:
            if _digest(actual[item.path]) != item.sha256:
                raise ValueError(f"固定任务文件哈希不匹配: {group}/{item.path}")

    @staticmethod
    def _validators(value: object) -> Mapping[str, Mapping[str, object]]:
        if not isinstance(value, list) or not value:
            raise ValueError("validators 必须是非空数组")
        parsed: dict[str, Mapping[str, object]] = {}
        for index, item in enumerate(value):
            if not isinstance(item, Mapping) or set(item) != {
                "kind", "commands", "timeout_seconds"
            }:
                raise ValueError(f"validators[{index}] 字段无效")
            kind = item["kind"]
            if kind not in {"core:build", "core:test", "core:cli"}:
                raise ValueError(f"不支持的固定 Validator: {kind}")
            if kind in parsed:
                raise ValueError(f"固定 Validator 重复: {kind}")
            commands = item["commands"]
            if not isinstance(commands, list) or not commands:
                raise ValueError("Validator commands 必须是非空数组")
            normalized: list[Mapping[str, object]] = []
            for command in commands:
                if not isinstance(command, Mapping) or "argv" not in command:
                    raise ValueError("Validator command 缺少 argv")
                allowed_fields = {
                    "argv", "expected_exit_code", "stdout_contains",
                    "stderr_contains", "reject_zero_tests",
                }
                if set(command) - allowed_fields:
                    raise ValueError("Validator command 包含未知字段")
                argv = command["argv"]
                if not isinstance(argv, list) or not argv or not all(
                    isinstance(part, str) and part for part in argv
                ):
                    raise ValueError("Validator argv 必须是非空字符串数组")
                expected_exit = command.get("expected_exit_code", 0)
                if not isinstance(expected_exit, int) or isinstance(
                    expected_exit, bool
                ):
                    raise ValueError("expected_exit_code 必须是整数")

                def strings(name: str) -> tuple[str, ...]:
                    raw = command.get(name, [])
                    if not isinstance(raw, list) or not all(
                        isinstance(value, str) and value for value in raw
                    ):
                        raise ValueError(f"{name} 必须是字符串数组")
                    return tuple(raw)

                reject_zero = command.get("reject_zero_tests", False)
                if not isinstance(reject_zero, bool):
                    raise ValueError("reject_zero_tests 必须是布尔值")
                normalized.append(MappingProxyType({
                    "argv": tuple(argv),
                    "expected_exit_code": expected_exit,
                    "stdout_contains": strings("stdout_contains"),
                    "stderr_contains": strings("stderr_contains"),
                    "reject_zero_tests": reject_zero,
                }))
            timeout = item["timeout_seconds"]
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
                raise ValueError("Validator timeout_seconds 必须是数字")
            if timeout <= 0 or timeout > 10:
                raise ValueError("Validator timeout_seconds 超出固定上限")
            parsed[kind] = MappingProxyType({
                "commands": tuple(normalized),
                "timeout_seconds": timeout,
            })
        return MappingProxyType(parsed)
