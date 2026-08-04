from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    READ_PROJECT = "read_project"
    PROPOSE_CHANGES = "propose_changes"
    WRITE_PROJECT = "write_project"
    RUN_VERIFICATION = "run_verification"
    REVIEW_CHANGES = "review_changes"


@dataclass(frozen=True)
class RoleSpec:
    """一次执行所需的职责和能力，不绑定 Agent、模型或供应商。"""

    name: str
    objective: str
    capabilities: frozenset[Capability]
    instructions: tuple[str, ...] = ()

    def allows(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def model_input(self) -> dict[str, object]:
        return {
            "name": self.name,
            "objective": self.objective,
            "capabilities": sorted(item.value for item in self.capabilities),
            "instructions": list(self.instructions),
        }


class RoleRegistry:
    def __init__(self, roles: tuple[RoleSpec, ...] = ()) -> None:
        self._roles: dict[str, RoleSpec] = {}
        for role in roles:
            self.register(role)

    def register(self, role: RoleSpec) -> None:
        if not role.name.strip():
            raise ValueError("角色名称不能为空")
        self._roles[role.name] = role

    def get(self, name: str) -> RoleSpec:
        try:
            return self._roles[name]
        except KeyError as exc:
            raise KeyError(f"未注册角色: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._roles))


PLANNER = RoleSpec(
    "planner",
    "理解需求、拆分工作并明确验收边界",
    frozenset({Capability.READ_PROJECT}),
    ("不得写文件或执行命令", "不得扩大用户授权范围"),
)
IMPLEMENTER = RoleSpec(
    "implementer",
    "根据任务生成最小且完整的代码变更",
    frozenset(
        {Capability.READ_PROJECT, Capability.PROPOSE_CHANGES, Capability.WRITE_PROJECT}
    ),
    ("只修改允许路径", "不得修改权限和验收标准"),
)
REVIEWER = RoleSpec(
    "reviewer",
    "独立检查代码质量、风险和需求覆盖情况",
    frozenset({Capability.READ_PROJECT, Capability.REVIEW_CHANGES}),
    ("保持只读", "不得批准自己实施的变更"),
)
TESTER = RoleSpec(
    "tester",
    "独立执行获准的验证并提供可复现证据",
    frozenset({Capability.READ_PROJECT, Capability.RUN_VERIFICATION}),
    ("不得修改项目文件", "只运行白名单命令"),
)
FIXER = RoleSpec(
    "fixer",
    "依据验证反馈修复失败且避免无关改动",
    frozenset(
        {Capability.READ_PROJECT, Capability.PROPOSE_CHANGES, Capability.WRITE_PROJECT}
    ),
    ("优先处理验证反馈", "保持已通过行为不变"),
)

DEFAULT_ROLES = RoleRegistry((PLANNER, IMPLEMENTER, REVIEWER, TESTER, FIXER))

