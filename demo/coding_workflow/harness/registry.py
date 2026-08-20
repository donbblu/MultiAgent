from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping


class WorkerSelectionCode(str, Enum):
    SELECTED = "selected"
    ROLE_UNAVAILABLE = "role_unavailable"
    MISSING_CAPABILITY = "missing_capability"
    INPUT_PROTOCOL_MISMATCH = "input_protocol_mismatch"
    OUTPUT_PROTOCOL_MISMATCH = "output_protocol_mismatch"
    POLICY_MISMATCH = "policy_mismatch"
    SEPARATION_CONFLICT = "separation_conflict"
    UNAVAILABLE = "unavailable"


def _string_set(value: object, field_name: str) -> frozenset[str]:
    if not isinstance(value, (tuple, list, set, frozenset)):
        raise ValueError(f"{field_name} 必须是字符串集合")
    items = frozenset(value)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise ValueError(f"{field_name} 不能包含空值")
    return items


@dataclass(frozen=True)
class WorkerDescriptor:
    """Worker 的可路由声明；不持有模型客户端或执行实例。"""

    worker_id: str
    role: str
    capabilities: frozenset[str] = frozenset()
    input_protocols: frozenset[str] = frozenset()
    output_protocols: frozenset[str] = frozenset()
    policy_tags: frozenset[str] = frozenset()
    principal_id: str = ""
    priority: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or not self.worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("Worker role 不能为空")
        for name in (
            "capabilities", "input_protocols", "output_protocols", "policy_tags"
        ):
            object.__setattr__(self, name, _string_set(getattr(self, name), name))
        principal = self.principal_id or self.worker_id
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("principal_id 不能为空")
        object.__setattr__(self, "principal_id", principal)
        if not isinstance(self.priority, int):
            raise ValueError("Worker priority 必须是整数")
        if not isinstance(self.enabled, bool):
            raise ValueError("Worker enabled 必须是布尔值")

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "worker_id": self.worker_id,
            "role": self.role,
            "capabilities": sorted(self.capabilities),
            "input_protocols": sorted(self.input_protocols),
            "output_protocols": sorted(self.output_protocols),
            "policy_tags": sorted(self.policy_tags),
            "principal_id": self.principal_id,
            "priority": self.priority,
            "enabled": self.enabled,
        })


@dataclass(frozen=True)
class WorkerSelectionRequest:
    task_id: str
    role: str
    required_capabilities: frozenset[str] = frozenset()
    input_protocols: frozenset[str] = frozenset()
    output_protocols: frozenset[str] = frozenset()
    required_policy_tags: frozenset[str] = frozenset()
    excluded_principal_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("选择请求 task_id 不能为空")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("选择请求 role 不能为空")
        for name in (
            "required_capabilities", "input_protocols", "output_protocols",
            "required_policy_tags", "excluded_principal_ids",
        ):
            object.__setattr__(self, name, _string_set(getattr(self, name), name))


@dataclass(frozen=True)
class WorkerCandidateDecision:
    worker_id: str
    role: str
    eligible: bool
    rejected_at: str = ""
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "worker_id": self.worker_id,
            "role": self.role,
            "eligible": self.eligible,
            "rejected_at": self.rejected_at,
            "reasons": list(self.reasons),
        })


@dataclass(frozen=True)
class WorkerSelectionDecision:
    task_id: str
    role: str
    code: WorkerSelectionCode
    selected_worker_id: str = ""
    selected_principal_id: str = ""
    reason: str = ""
    candidates: tuple[WorkerCandidateDecision, ...] = ()

    @property
    def selected(self) -> bool:
        return self.code is WorkerSelectionCode.SELECTED

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "task_id": self.task_id,
            "role": self.role,
            "code": self.code.value,
            "selected_worker_id": self.selected_worker_id,
            "selected_principal_id": self.selected_principal_id,
            "reason": self.reason,
            "candidates": [dict(item.to_dict()) for item in self.candidates],
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "WorkerSelectionDecision":
        return cls(
            str(value["task_id"]),
            str(value["role"]),
            WorkerSelectionCode(str(value["code"])),
            str(value.get("selected_worker_id", "")),
            str(value.get("selected_principal_id", "")),
            str(value.get("reason", "")),
            tuple(
                WorkerCandidateDecision(
                    str(item["worker_id"]), str(item["role"]),
                    bool(item["eligible"]), str(item.get("rejected_at", "")),
                    tuple(str(reason) for reason in item.get("reasons", ())),
                )
                for item in value.get("candidates", ())
            ),
        )


class WorkerSelectionError(RuntimeError):
    """没有满足硬条件的 Worker；调用方必须 blocked，不能静默降级。"""

    def __init__(self, decision: WorkerSelectionDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason or decision.code.value)

    def to_dict(self) -> Mapping[str, object]:
        return self.decision.to_dict()


@dataclass(frozen=True)
class WorkerRegistrySnapshot:
    descriptors: tuple[WorkerDescriptor, ...]
    availability: Mapping[str, bool]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "availability", MappingProxyType(dict(self.availability))
        )


@dataclass(frozen=True)
class WorkerSelection:
    worker: object
    descriptor: WorkerDescriptor
    decision: WorkerSelectionDecision


@dataclass
class _Registration:
    descriptor: WorkerDescriptor
    worker: object
    availability: Callable[[], bool]


class WorkerRegistry:
    """Role-first Worker 路由；硬条件过滤后只用稳定键决胜。"""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}
        self._audit: list[WorkerSelectionDecision] = []
        self._legacy_principals: dict[int, str] = {}
        self._legacy_counter = 0
        self._lock = RLock()

    def register(self, role: str, worker: object, *, replace: bool = False) -> None:
        """兼容旧 API；每个 Role 保留一个 role 名称的默认 Worker。"""
        if not isinstance(role, str) or not role.strip() or worker is None:
            raise ValueError("role 和 worker 不能为空")
        with self._lock:
            identity = id(worker)
            principal = self._legacy_principals.get(identity)
            if principal is None:
                self._legacy_counter += 1
                principal = f"legacy-instance-{self._legacy_counter:04d}"
                self._legacy_principals[identity] = principal
        self.register_worker(
            WorkerDescriptor(role, role, principal_id=principal),
            worker,
            replace=replace,
        )

    def register_worker(
        self,
        descriptor: WorkerDescriptor,
        worker: object,
        *,
        availability: Callable[[], bool] | None = None,
        replace: bool = False,
    ) -> None:
        if not isinstance(descriptor, WorkerDescriptor):
            raise TypeError("descriptor 必须是 WorkerDescriptor")
        if worker is None:
            raise ValueError("worker 不能为空")
        checker = availability or (lambda: True)
        if not callable(checker):
            raise TypeError("availability 必须可调用")
        with self._lock:
            if descriptor.worker_id in self._registrations and not replace:
                raise ValueError(f"Worker 已注册: {descriptor.worker_id}")
            self._registrations[descriptor.worker_id] = _Registration(
                descriptor, worker, checker
            )

    def _available(self, registration: _Registration) -> tuple[bool, str]:
        if not registration.descriptor.enabled:
            return False, "descriptor disabled"
        try:
            return (True, "") if registration.availability() else (
                False, "availability probe returned false"
            )
        except Exception as exc:
            return False, f"availability probe error: {type(exc).__name__}"

    def select(
        self,
        request: WorkerSelectionRequest,
        *,
        record_audit: bool = True,
    ) -> WorkerSelection:
        if not isinstance(request, WorkerSelectionRequest):
            raise TypeError("request 必须是 WorkerSelectionRequest")
        with self._lock:
            registrations = tuple(
                self._registrations[key] for key in sorted(self._registrations)
            )
            rejected: dict[str, tuple[str, tuple[str, ...]]] = {}
            pool = list(registrations)

            def apply(
                code: WorkerSelectionCode,
                predicate: Callable[[_Registration], tuple[bool, tuple[str, ...]]],
            ) -> WorkerSelectionDecision | None:
                nonlocal pool
                kept: list[_Registration] = []
                for registration in pool:
                    ok, reasons = predicate(registration)
                    if ok:
                        kept.append(registration)
                    else:
                        rejected[registration.descriptor.worker_id] = (
                            code.value, reasons
                        )
                pool = kept
                if pool:
                    return None
                decision = self._decision(request, code, rejected, registrations)
                if record_audit:
                    self._audit.append(decision)
                return decision

            decision = apply(
                WorkerSelectionCode.ROLE_UNAVAILABLE,
                lambda item: (
                    item.descriptor.role == request.role,
                    (f"role={item.descriptor.role} != {request.role}",),
                ),
            )
            if decision:
                raise WorkerSelectionError(decision)

            filters = (
                (
                    WorkerSelectionCode.MISSING_CAPABILITY,
                    lambda item: self._subset_reason(
                        request.required_capabilities,
                        item.descriptor.capabilities,
                        "capabilities",
                    ),
                ),
                (
                    WorkerSelectionCode.INPUT_PROTOCOL_MISMATCH,
                    lambda item: self._subset_reason(
                        request.input_protocols,
                        item.descriptor.input_protocols,
                        "input_protocols",
                    ),
                ),
                (
                    WorkerSelectionCode.OUTPUT_PROTOCOL_MISMATCH,
                    lambda item: self._subset_reason(
                        request.output_protocols,
                        item.descriptor.output_protocols,
                        "output_protocols",
                    ),
                ),
                (
                    WorkerSelectionCode.POLICY_MISMATCH,
                    lambda item: self._subset_reason(
                        request.required_policy_tags,
                        item.descriptor.policy_tags,
                        "policy_tags",
                    ),
                ),
                (
                    WorkerSelectionCode.SEPARATION_CONFLICT,
                    lambda item: (
                        item.descriptor.principal_id
                        not in request.excluded_principal_ids,
                        (f"principal={item.descriptor.principal_id} is excluded",),
                    ),
                ),
                (
                    WorkerSelectionCode.UNAVAILABLE,
                    lambda item: (
                        lambda status: (status[0], (status[1],) if status[1] else ())
                    )(self._available(item)),
                ),
            )
            for code, predicate in filters:
                decision = apply(code, predicate)
                if decision:
                    raise WorkerSelectionError(decision)

            selected = sorted(
                pool,
                key=lambda item: (-item.descriptor.priority, item.descriptor.worker_id),
            )[0]
            for registration in pool:
                if registration is not selected:
                    rejected[registration.descriptor.worker_id] = (
                        "tie_break",
                        ("lower priority or lexicographically later worker_id",),
                    )
            decision = self._decision(
                request, WorkerSelectionCode.SELECTED, rejected, registrations,
                selected=selected.descriptor,
            )
            if record_audit:
                self._audit.append(decision)
            return WorkerSelection(selected.worker, selected.descriptor, decision)

    @staticmethod
    def _subset_reason(
        required: frozenset[str], offered: frozenset[str], label: str
    ) -> tuple[bool, tuple[str, ...]]:
        missing = sorted(required - offered)
        return not missing, ((f"missing {label}: {missing}",) if missing else ())

    @staticmethod
    def _decision(
        request: WorkerSelectionRequest,
        code: WorkerSelectionCode,
        rejected: Mapping[str, tuple[str, tuple[str, ...]]],
        registrations: tuple[_Registration, ...],
        *,
        selected: WorkerDescriptor | None = None,
    ) -> WorkerSelectionDecision:
        candidates = tuple(
            WorkerCandidateDecision(
                item.descriptor.worker_id,
                item.descriptor.role,
                selected is not None and item.descriptor.worker_id == selected.worker_id,
                rejected.get(item.descriptor.worker_id, ("", ()))[0],
                rejected.get(item.descriptor.worker_id, ("", ()))[1],
            )
            for item in registrations
        )
        if selected is not None:
            reason = (
                f"selected {selected.worker_id} after role/capability/protocol/"
                "policy/separation/availability filters and stable tie-break"
            )
        else:
            reason = (
                f"task {request.task_id} blocked at {code.value}; "
                "Runtime did not cross Role or lower requirements"
            )
        return WorkerSelectionDecision(
            request.task_id,
            request.role,
            code,
            selected.worker_id if selected else "",
            selected.principal_id if selected else "",
            reason,
            candidates,
        )

    def resolve(self, role: str, *, required: bool = True) -> object | None:
        """兼容旧 API；多实现时同样按稳定规则选择。"""
        try:
            return self.select(
                WorkerSelectionRequest(f"legacy-resolve:{role}", role),
                record_audit=False,
            ).worker
        except WorkerSelectionError:
            if required:
                raise KeyError(f"角色没有可用 Worker: {role}")
            return None

    def snapshot(self) -> WorkerRegistrySnapshot:
        with self._lock:
            registrations = tuple(
                self._registrations[key] for key in sorted(self._registrations)
            )
            availability = {
                item.descriptor.worker_id: self._available(item)[0]
                for item in registrations
            }
            return WorkerRegistrySnapshot(
                tuple(item.descriptor for item in registrations), availability
            )

    def audit_snapshot(self) -> tuple[WorkerSelectionDecision, ...]:
        with self._lock:
            return tuple(self._audit)
