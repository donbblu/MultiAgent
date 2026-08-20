from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

from .scenario import ScenarioProfile


class PluginError(RuntimeError):
    """场景插件不满足 Core 插件边界。"""


class PluginCompatibilityError(PluginError):
    pass


class PluginRegistrationError(PluginError):
    pass


class PluginUnavailableError(PluginError):
    pass


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{field_name} 必须以小写字母开头，且只包含小写字母、数字、_ 或 -"
        )
    return value


def _unique_identifiers(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError(f"{field_name} 必须是字符串数组")
    parsed = tuple(_identifier(item, field_name) for item in values)
    if len(set(parsed)) != len(parsed):
        raise ValueError(f"{field_name} 不能重复")
    return parsed


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    version: str
    core_api_version: str
    scenarios: tuple[str, ...]
    required_capabilities: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_id", _identifier(self.plugin_id, "plugin_id"))
        for field_name in ("version", "core_api_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 不能为空")
            object.__setattr__(self, field_name, value.strip())
        scenarios = _unique_identifiers(self.scenarios, "scenarios")
        if not scenarios:
            raise ValueError("场景插件必须至少声明一个 scenario")
        object.__setattr__(self, "scenarios", scenarios)
        object.__setattr__(
            self,
            "required_capabilities",
            _unique_identifiers(
                self.required_capabilities, "required_capabilities"
            ),
        )
        if not isinstance(self.optional_dependencies, (tuple, list)):
            raise ValueError("optional_dependencies 必须是非空字符串数组")
        dependencies = tuple(self.optional_dependencies)
        if not all(isinstance(item, str) and item.strip() for item in dependencies):
            raise ValueError("optional_dependencies 必须是非空字符串数组")
        normalized = tuple(item.strip() for item in dependencies)
        if len(set(normalized)) != len(normalized):
            raise ValueError("optional_dependencies 不能重复")
        object.__setattr__(self, "optional_dependencies", normalized)


ScenarioFactory = Callable[..., ScenarioProfile]


@dataclass(frozen=True)
class ScenarioRegistration:
    plugin_id: str
    plugin_version: str
    scenario: str
    factory: ScenarioFactory

    @property
    def reference(self) -> str:
        return f"{self.plugin_id}:{self.scenario}"

    def create(self, *args: object, **kwargs: object) -> "RegisteredScenarioProfile":
        profile = self.factory(*args, **kwargs)
        required = (
            "name",
            "max_rework_rounds",
            "build_round",
            "decide",
            "finalize",
            "restore_result",
        )
        missing = tuple(name for name in required if not hasattr(profile, name))
        if missing:
            raise PluginRegistrationError(
                f"场景工厂 {self.reference} 返回值缺少接口: {', '.join(missing)}"
            )
        if getattr(profile, "name") != self.scenario:
            raise PluginRegistrationError(
                f"场景工厂 {self.reference} 返回了不匹配的 name"
            )
        return RegisteredScenarioProfile(self, profile)


@dataclass(frozen=True)
class RegisteredScenarioProfile:
    """为 ScenarioRuntime 附加可恢复的插件身份，不修改插件 Profile。"""

    registration: ScenarioRegistration
    profile: ScenarioProfile

    @property
    def plugin_id(self) -> str:
        return self.registration.plugin_id

    @property
    def plugin_version(self) -> str:
        return self.registration.plugin_version

    @property
    def name(self) -> str:
        return self.registration.scenario

    @property
    def max_rework_rounds(self) -> int:
        return self.profile.max_rework_rounds

    def build_round(self, state, lifecycle):
        return self.profile.build_round(state, lifecycle)

    def decide(self, state, execution, artifacts):
        return self.profile.decide(state, execution, artifacts)

    def finalize(self, state, artifacts, decision):
        return self.profile.finalize(state, artifacts, decision)

    def restore_result(self, result_artifact_ref, artifacts):
        return self.profile.restore_result(result_artifact_ref, artifacts)


class PluginRegistrationContext:
    """插件只能在注册阶段提交 Manifest 已声明的场景工厂。"""

    def __init__(self, manifest: PluginManifest) -> None:
        self._manifest = manifest
        self._scenarios: dict[str, ScenarioFactory] = {}
        self._closed = False

    @property
    def plugin_id(self) -> str:
        return self._manifest.plugin_id

    def register_scenario(
        self, scenario: str, factory: ScenarioFactory
    ) -> None:
        if self._closed:
            raise PluginRegistrationError("插件注册上下文已经关闭")
        name = _identifier(scenario, "scenario")
        if name not in self._manifest.scenarios:
            raise PluginRegistrationError(
                f"插件 {self.plugin_id} 注册了 Manifest 未声明的场景: {name}"
            )
        if name in self._scenarios:
            raise PluginRegistrationError(
                f"插件 {self.plugin_id} 重复注册场景: {name}"
            )
        if not callable(factory):
            raise PluginRegistrationError("场景 factory 必须可调用")
        self._scenarios[name] = factory

    def _finish(self) -> Mapping[str, ScenarioFactory]:
        self._closed = True
        missing = set(self._manifest.scenarios) - set(self._scenarios)
        if missing:
            raise PluginRegistrationError(
                f"插件 {self.plugin_id} 未注册已声明场景: {sorted(missing)}"
            )
        return MappingProxyType(dict(self._scenarios))

    def _abort(self) -> None:
        self._closed = True
        self._scenarios.clear()


class ScenarioPlugin(Protocol):
    @property
    def manifest(self) -> PluginManifest: ...

    def register(self, context: PluginRegistrationContext) -> None: ...


class PluginRegistry:
    """Core 的显式可信插件注册表；不负责动态导入或安装插件。"""

    def __init__(self, *, core_api_version: str) -> None:
        if not isinstance(core_api_version, str) or not core_api_version.strip():
            raise ValueError("core_api_version 不能为空")
        self.core_api_version = core_api_version.strip()
        self._plugins: dict[str, PluginManifest] = {}
        self._scenarios: dict[str, ScenarioRegistration] = {}
        self._lock = RLock()

    def register(self, plugin: ScenarioPlugin) -> PluginManifest:
        manifest = getattr(plugin, "manifest", None)
        if not isinstance(manifest, PluginManifest):
            raise PluginRegistrationError("插件必须提供 PluginManifest")
        if manifest.core_api_version != self.core_api_version:
            raise PluginCompatibilityError(
                f"插件 {manifest.plugin_id} 需要 Core API "
                f"{manifest.core_api_version}，当前为 {self.core_api_version}"
            )
        if not callable(getattr(plugin, "register", None)):
            raise PluginRegistrationError("插件必须实现 register(context)")
        with self._lock:
            if manifest.plugin_id in self._plugins:
                raise PluginRegistrationError(
                    f"插件已经注册: {manifest.plugin_id}"
                )

        context = PluginRegistrationContext(manifest)
        try:
            plugin.register(context)
            staged = context._finish()
        except Exception as exc:
            context._abort()
            if isinstance(exc, PluginError):
                raise
            raise PluginRegistrationError(
                f"插件 {manifest.plugin_id} 注册失败: {exc}"
            ) from exc

        registrations = {
            f"{manifest.plugin_id}:{name}": ScenarioRegistration(
                manifest.plugin_id, manifest.version, name, factory
            )
            for name, factory in staged.items()
        }
        with self._lock:
            if manifest.plugin_id in self._plugins:
                raise PluginRegistrationError(
                    f"插件已经注册: {manifest.plugin_id}"
                )
            self._plugins[manifest.plugin_id] = manifest
            self._scenarios.update(registrations)
        return manifest

    def manifest(self, plugin_id: str) -> PluginManifest:
        with self._lock:
            try:
                return self._plugins[plugin_id]
            except KeyError as exc:
                raise PluginUnavailableError(
                    f"插件未启用: {plugin_id}"
                ) from exc

    def resolve_scenario(
        self, plugin_id: str, scenario: str
    ) -> ScenarioRegistration:
        reference = f"{plugin_id}:{scenario}"
        with self._lock:
            try:
                return self._scenarios[reference]
            except KeyError as exc:
                raise PluginUnavailableError(
                    f"场景插件未启用或未注册: {reference}"
                ) from exc

    def resolve_reference(self, reference: str) -> ScenarioRegistration:
        if reference.count(":") != 1:
            raise PluginUnavailableError(
                "场景引用必须使用 plugin_id:scenario 格式"
            )
        plugin_id, scenario = reference.split(":", 1)
        return self.resolve_scenario(plugin_id, scenario)

    def manifests(self) -> Mapping[str, PluginManifest]:
        with self._lock:
            return MappingProxyType(dict(self._plugins))

    def available_scenarios(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._scenarios))
