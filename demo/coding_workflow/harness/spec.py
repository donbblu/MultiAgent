from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class NodeSpec:
    """声明一个工作流节点；节点只引用角色，不绑定 Agent 或模型。"""

    name: str
    role: str
    dependencies: tuple[str, ...] = ()
    optional: bool = False
    concurrency_group: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.role.strip():
            raise ValueError("工作流节点的 name 和 role 不能为空")


class WorkflowSpec:
    """经过校验的不可变 DAG 描述。"""

    def __init__(self, name: str, nodes: tuple[NodeSpec, ...]) -> None:
        if not name.strip() or not nodes:
            raise ValueError("工作流必须有名称和至少一个节点")
        by_name = {node.name: node for node in nodes}
        if len(by_name) != len(nodes):
            raise ValueError("工作流节点名称不能重复")
        for node in nodes:
            missing = set(node.dependencies) - by_name.keys()
            if missing:
                raise ValueError(f"节点 {node.name} 依赖不存在: {sorted(missing)}")
        self.name = name
        self._nodes: Mapping[str, NodeSpec] = MappingProxyType(by_name)
        self._validate_acyclic()

    @property
    def nodes(self) -> Mapping[str, NodeSpec]:
        return self._nodes

    def node(self, name: str) -> NodeSpec:
        try:
            return self._nodes[name]
        except KeyError as exc:
            raise KeyError(f"工作流节点未注册: {name}") from exc

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError(f"工作流存在环: {name}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in self._nodes[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in self._nodes:
            visit(name)


def coding_workflow_spec() -> WorkflowSpec:
    return WorkflowSpec(
        "coding-agent",
        (
            NodeSpec("plan", "planner"),
            NodeSpec("implement", "implementer", ("plan",)),
            NodeSpec("fix", "fixer", ("implement",), optional=True),
            NodeSpec("test", "tester", ("implement",), concurrency_group="quality"),
            NodeSpec("review", "reviewer", ("implement",), optional=True, concurrency_group="quality"),
        ),
    )
