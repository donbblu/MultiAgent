from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from .agent_executor import (
    AgentExecutionPermission,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionRuntime,
    AgentExecutionStateEnvelope,
    AgentExecutionStatus,
    AgentExecutor,
)
from .agent_runtime import AgentManager, MailboxManager
from .artifacts import Artifact, ArtifactStore
from .requirements import ValidatorProfile
from .role_assignment import RoleAssignmentManager, RoleAssignmentScheduler
from .runtime_domain import (
    AgentCandidate,
    AssignmentDecision,
    Message,
    RoleAssignmentPolicy,
    RoleRequirement,
    RuntimeActorType,
    RuntimeEvent,
    RuntimeProtocolError,
    ScopedRef,
    ScopedSnapshotRef,
    Thread,
    canonical_digest,
)
from .runtime_persistence import (
    SQLiteAgentExecutionStateStore,
    SQLiteProductHistoryStore,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)
from .truth import VerificationOutcome, VerificationRecord
from .validator_runtime import (
    ValidatorProfileRunner,
    ValidatorRegistry,
)


ProductClock = Callable[[], str]


class ProductTaskStatus(str, Enum):
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class ProductTaskConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProductTaskRequest:
    task_id: str
    prompt: str
    permission: AgentExecutionPermission
    timeout_seconds: float
    scope_id: str = "local"

    def __post_init__(self) -> None:
        for name in ("task_id", "prompt", "scope_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}不能为空")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.permission, AgentExecutionPermission):
            raise TypeError("permission必须是AgentExecutionPermission")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds必须大于0")


@dataclass(frozen=True)
class ProductAgentConfig:
    role_id: str
    agent_id: str
    session_id: str
    profile_id: str
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("role_id", "agent_id", "session_id", "profile_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}不能为空")
            object.__setattr__(self, name, value.strip())
        capabilities = tuple(self.capabilities)
        if not all(
            isinstance(item, str) and item.strip() for item in capabilities
        ):
            raise ValueError("capabilities必须是非空字符串数组")
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("capabilities不能重复")
        object.__setattr__(self, "capabilities", capabilities)


@dataclass(frozen=True)
class ProductTaskResult:
    task_id: str
    request_digest: str
    thread_id: str
    status: ProductTaskStatus
    planner_invocation_id: str
    recipient_invocation_id: str = ""
    recipient_agent_id: str = ""
    assignment_id: str = ""
    message_id: str = ""
    result_artifact_ref: str = ""
    verification_ref: str = ""
    validator_report_ref: str = ""
    validation_outcome: VerificationOutcome = VerificationOutcome.UNKNOWN
    error_code: str = ""

    def to_dict(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": "product-task-result/v2",
            "task_id": self.task_id,
            "request_digest": self.request_digest,
            "thread_id": self.thread_id,
            "status": self.status.value,
            "planner_invocation_id": self.planner_invocation_id,
            "recipient_invocation_id": self.recipient_invocation_id,
            "recipient_agent_id": self.recipient_agent_id,
            "assignment_id": self.assignment_id,
            "message_id": self.message_id,
            "result_artifact_ref": self.result_artifact_ref,
            "verification_ref": self.verification_ref,
            "validator_report_ref": self.validator_report_ref,
            "validation_outcome": self.validation_outcome.value,
            "error_code": self.error_code,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProductTaskResult":
        required = {
            "schema_version",
            "task_id",
            "request_digest",
            "thread_id",
            "status",
            "planner_invocation_id",
            "recipient_invocation_id",
            "recipient_agent_id",
            "assignment_id",
            "message_id",
            "result_artifact_ref",
            "verification_ref",
            "validator_report_ref",
            "validation_outcome",
            "error_code",
        }
        if set(value) != required or value.get(
            "schema_version"
        ) != "product-task-result/v2":
            raise ValueError("ProductTaskResult schema无效")
        return cls(
            task_id=value["task_id"],
            request_digest=value["request_digest"],
            thread_id=value["thread_id"],
            status=ProductTaskStatus(value["status"]),
            planner_invocation_id=value["planner_invocation_id"],
            recipient_invocation_id=value["recipient_invocation_id"],
            recipient_agent_id=value["recipient_agent_id"],
            assignment_id=value["assignment_id"],
            message_id=value["message_id"],
            result_artifact_ref=value["result_artifact_ref"],
            verification_ref=value["verification_ref"],
            validator_report_ref=value["validator_report_ref"],
            validation_outcome=VerificationOutcome(
                value["validation_outcome"]
            ),
            error_code=value["error_code"],
        )


@dataclass(frozen=True)
class _PlannerDelegation:
    recipient_role: str
    task_instruction: str
    required_capabilities: tuple[str, ...]
    acceptance_summary: str


class LocalProductTaskService:
    """First local product composition over existing Runtime components."""

    def __init__(
        self,
        database: SQLiteRuntimeDatabase,
        *,
        executor: AgentExecutor,
        workspace_root: Path,
        agents: tuple[ProductAgentConfig, ...],
        assignment_policy: RoleAssignmentPolicy,
        validator_profile: ValidatorProfile,
        validator_registry: ValidatorRegistry,
        artifacts: ArtifactStore,
        clock: ProductClock,
    ) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database必须是SQLiteRuntimeDatabase")
        if not callable(getattr(executor, "run", None)):
            raise TypeError("executor必须实现AgentExecutor")
        root = Path(workspace_root).resolve()
        if not root.is_dir():
            raise ValueError("workspace_root必须是已存在目录")
        configured = tuple(agents)
        if not configured or not all(
            isinstance(item, ProductAgentConfig) for item in configured
        ):
            raise TypeError("agents必须包含ProductAgentConfig")
        planners = tuple(item for item in configured if item.role_id == "planner")
        if len(planners) != 1:
            raise ValueError("首切必须且只能配置一个planner")
        if len({item.agent_id for item in configured}) != len(configured):
            raise ValueError("agent_id不能重复")
        if not isinstance(assignment_policy, RoleAssignmentPolicy):
            raise TypeError("assignment_policy必须是RoleAssignmentPolicy")
        if not isinstance(validator_profile, ValidatorProfile):
            raise TypeError("validator_profile必须是ValidatorProfile")
        if not isinstance(validator_registry, ValidatorRegistry):
            raise TypeError("validator_registry必须是ValidatorRegistry")
        if not isinstance(artifacts, ArtifactStore):
            raise TypeError("artifacts必须是ArtifactStore")
        if not callable(clock):
            raise TypeError("clock必须可调用")
        self._database = database
        self._executor = executor
        self._workspace_root = root
        self._agents = configured
        self._planner = planners[0]
        self._assignment_policy = assignment_policy
        self._artifacts = artifacts
        self._validator = ValidatorProfileRunner(
            validator_profile,
            validator_registry,
            artifacts,
        )
        self._history = SQLiteProductHistoryStore(database)
        self._clock = clock

    def get_task_result(
        self,
        task_id: str,
        *,
        scope_id: str = "local",
    ) -> ProductTaskResult | None:
        payload = self._history.result_for(
            scope_id=scope_id,
            task_id=task_id,
        )
        return None if payload is None else ProductTaskResult.from_dict(payload)

    def get_artifact(self, reference: str) -> Artifact:
        return self._history.artifact_for(reference)

    def get_verification(self, reference: str) -> VerificationRecord:
        return self._history.verification_for(reference)

    def run(self, request: ProductTaskRequest) -> ProductTaskResult:
        if not isinstance(request, ProductTaskRequest):
            raise TypeError("request必须是ProductTaskRequest")
        existing = self.get_task_result(
            request.task_id,
            scope_id=request.scope_id,
        )
        if existing is not None:
            if existing.request_digest != self._request_digest(request):
                raise ProductTaskConflictError("task_request_conflict")
            return existing
        ids = self._ids(request)
        thread = self._ensure_thread_and_agents(request, ids)
        execution_store = SQLiteAgentExecutionStateStore(self._database)
        execution_runtime = AgentExecutionRuntime(
            executor=self._executor,
            state_authority=execution_store,
            replay_store=execution_store,
            session_store=execution_store,
        )

        planner_state = self._state(
            request,
            invocation_id=ids["planner_invocation_id"],
            content=request.prompt,
        )
        self._record_state(
            execution_store,
            ids["planner_invocation_id"],
            planner_state,
        )
        planner_result = execution_runtime.run(AgentExecutionRequest(
            invocation_id=ids["planner_invocation_id"],
            thread_id=thread.thread_id,
            agent_id=self._planner.agent_id,
            prompt=self._planner_prompt(request.prompt),
            workspace_root=self._workspace_root,
            permission=request.permission,
            timeout_seconds=request.timeout_seconds,
            state_envelope=planner_state,
        ))
        if (
            not isinstance(planner_result, AgentExecutionResult)
            or planner_result.status is not AgentExecutionStatus.COMPLETED
        ):
            return self._record_result(
                request,
                self._result(
                    request,
                    ids,
                    ProductTaskStatus.FAILED,
                    error_code="planner_execution_failed",
                ),
            )
        try:
            delegation = self._parse_planner_delegation(
                planner_result.final_message
            )
        except (ValueError, RuntimeProtocolError, json.JSONDecodeError):
            return self._record_result(
                request,
                self._result(
                    request,
                    ids,
                    ProductTaskStatus.NEEDS_INPUT,
                    error_code="invalid_planner_delegation",
                ),
            )

        candidates = self._candidates(request.scope_id, delegation.recipient_role)
        requirement = RoleRequirement(
            requirement_id=ids["requirement_id"],
            scope_id=request.scope_id,
            thread_ref=thread.reference,
            work_ref=self._ref(
                request.scope_id,
                "core:invocation",
                ids["recipient_invocation_id"],
            ),
            role_ref=self._role_ref(
                request.scope_id,
                delegation.recipient_role,
            ),
            required_capabilities=delegation.required_capabilities,
            created_at=self._clock(),
        )
        assignment = RoleAssignmentScheduler().decide(
            assignment_id=ids["assignment_id"],
            requirement=requirement,
            candidates=candidates,
            policy=self._assignment_policy,
            created_at=self._clock(),
        )
        if assignment.decision is not AssignmentDecision.ASSIGNED:
            RoleAssignmentManager(self._database).record(assignment)
            return self._record_result(
                request,
                self._result(
                    request,
                    ids,
                    ProductTaskStatus.NEEDS_INPUT,
                    error_code=assignment.reason_code,
                    assignment_id=assignment.assignment_id,
                ),
            )

        message = Message(
            message_id=ids["message_id"],
            scope_id=request.scope_id,
            thread_ref=thread.reference,
            turn_ref=self._ref(
                request.scope_id,
                "core:turn",
                ids["turn_id"],
            ),
            sequence=1,
            sender_ref=self._agent_ref(
                request.scope_id,
                self._planner.agent_id,
            ),
            recipient_refs=(assignment.selected_agent_instance_ref,),
            kind="core:assigned_work",
            body=delegation.task_instruction,
            causation_ref=self._ref(
                request.scope_id,
                "core:invocation",
                ids["planner_invocation_id"],
            ),
            created_at=self._clock(),
        )
        RoleAssignmentManager(self._database).record_and_enqueue(
            assignment,
            message,
            enqueued_at=self._clock(),
        )
        delivery = MailboxManager(
            self._database,
            clock=self._clock,
        ).receive_next(
            scope_id=request.scope_id,
            thread_id=thread.thread_id,
            agent_instance_id=assignment.selected_agent_instance_ref.entity_id,
            agent_session_id=assignment.selected_agent_session_ref.entity_id,
        )
        if delivery is None:
            return self._record_result(
                request,
                self._result(
                    request,
                    ids,
                    ProductTaskStatus.FAILED,
                    error_code="mailbox_delivery_missing",
                    assignment_id=assignment.assignment_id,
                    message_id=message.message_id,
                ),
            )

        recipient_state = self._state(
            request,
            invocation_id=ids["recipient_invocation_id"],
            content=message.body,
        )
        self._record_state(
            execution_store,
            ids["recipient_invocation_id"],
            recipient_state,
        )
        recipient_result = execution_runtime.run(AgentExecutionRequest(
            invocation_id=ids["recipient_invocation_id"],
            thread_id=thread.thread_id,
            agent_id=assignment.selected_agent_instance_ref.entity_id,
            prompt=self._recipient_prompt(
                request,
                delegation,
                delivery.message,
            ),
            workspace_root=self._workspace_root,
            permission=request.permission,
            timeout_seconds=request.timeout_seconds,
            state_envelope=recipient_state,
        ))
        if (
            not isinstance(recipient_result, AgentExecutionResult)
            or recipient_result.status is not AgentExecutionStatus.COMPLETED
        ):
            return self._record_result(
                request,
                self._result(
                    request,
                    ids,
                    ProductTaskStatus.FAILED,
                    error_code="recipient_execution_failed",
                    recipient_agent_id=(
                        assignment.selected_agent_instance_ref.entity_id
                    ),
                    assignment_id=assignment.assignment_id,
                    message_id=message.message_id,
                ),
            )

        artifact_ref = self._artifacts.put(Artifact.create(
            "product-result",
            request.task_id,
            recipient_result.final_message,
            kind="agent_result",
            metadata={
                "runtime_provenance": {
                    "principal_id": assignment.selected_agent_instance_ref.entity_id,
                    "role": delegation.recipient_role,
                    "task_id": request.task_id,
                    "invocation_id": ids["recipient_invocation_id"],
                }
            },
        ))
        validation = self._validator.run(
            task_id=request.task_id,
            subject_refs=(artifact_ref,),
        )
        status = {
            VerificationOutcome.PASSED: ProductTaskStatus.VALIDATED,
            VerificationOutcome.FAILED: ProductTaskStatus.VALIDATION_FAILED,
            VerificationOutcome.UNKNOWN: ProductTaskStatus.NEEDS_INPUT,
        }[validation.outcome]
        result = self._result(
            request,
            ids,
            status,
            recipient_agent_id=assignment.selected_agent_instance_ref.entity_id,
            assignment_id=assignment.assignment_id,
            message_id=message.message_id,
            result_artifact_ref=artifact_ref,
            verification_ref=validation.verification_ref,
            validator_report_ref=validation.report_artifact_ref,
            validation_outcome=validation.outcome,
        )
        return self._record_result(request, result)

    def _record_result(
        self,
        request: ProductTaskRequest,
        result: ProductTaskResult,
    ) -> ProductTaskResult:
        self._history.record(
            scope_id=request.scope_id,
            task_id=request.task_id,
            result_payload=result.to_dict(),
            artifacts=self._artifacts,
            verification_ref=result.verification_ref,
        )
        return result

    def _ensure_thread_and_agents(
        self,
        request: ProductTaskRequest,
        ids: Mapping[str, str],
    ) -> Thread:
        instant = self._clock()
        user_ref = self._ref(request.scope_id, "core:principal", "local-user")
        agent_refs = tuple(
            self._agent_ref(request.scope_id, item.agent_id)
            for item in self._agents
        )
        thread = Thread(
            thread_id=ids["thread_id"],
            scope_id=request.scope_id,
            title=request.prompt[:120],
            participant_refs=(user_ref, *agent_refs),
            created_at=instant,
            updated_at=instant,
        )
        event = RuntimeEvent(
            scope_id=request.scope_id,
            event_id=ids["thread_event_id"],
            event_type="core:thread_created",
            aggregate_ref=thread.reference,
            aggregate_version=1,
            sequence_no=1,
            trace_id=ids["trace_id"],
            correlation_id=ids["correlation_id"],
            actor_type=RuntimeActorType.USER,
            actor_ref=user_ref,
            idempotency_key=ids["thread_idempotency_key"],
            occurred_at=instant,
            recorded_at=instant,
            thread_ref=thread.reference,
            payload={"state": "open"},
        )
        store = SQLiteThreadEventStore(self._database)
        with self._database.unit_of_work() as uow:
            store.apply(uow, ThreadEventMutation(0, thread, event))
            uow.commit()
        manager = AgentManager(self._database, clock=self._clock)
        for agent in self._agents:
            manager.create_agent(
                agent_instance_id=agent.agent_id,
                agent_session_id=agent.session_id,
                scope_id=request.scope_id,
                thread_ref=thread.reference,
                profile_ref=self._ref(
                    request.scope_id,
                    "core:agent_profile",
                    agent.profile_id,
                ),
                principal_id=f"principal:{agent.agent_id}",
                created_at=instant,
            )
        return thread

    def _candidates(
        self,
        scope_id: str,
        role_id: str,
    ) -> tuple[AgentCandidate, ...]:
        return tuple(
            AgentCandidate(
                agent_instance_ref=self._agent_ref(scope_id, item.agent_id),
                agent_session_ref=self._ref(
                    scope_id,
                    "core:agent_session",
                    item.session_id,
                ),
                profile_ref=self._ref(
                    scope_id,
                    "core:agent_profile",
                    item.profile_id,
                ),
                capabilities=item.capabilities,
            )
            for item in self._agents
            if item.role_id == role_id and item.role_id != "planner"
        )

    @staticmethod
    def _record_state(
        store: SQLiteAgentExecutionStateStore,
        invocation_id: str,
        state: AgentExecutionStateEnvelope,
    ) -> None:
        with store._database.unit_of_work() as uow:
            store.record_expected(uow, invocation_id, state)
            uow.commit()

    @staticmethod
    def _parse_planner_delegation(raw: str) -> _PlannerDelegation:
        value = json.loads(raw)
        required = {
            "schema_version",
            "action",
            "recipient_role",
            "task_instruction",
            "required_capabilities",
            "acceptance_summary",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("planner delegation字段无效")
        if value["schema_version"] != "planner-delegation/v1":
            raise ValueError("planner delegation版本无效")
        if value["action"] != "delegate_task":
            raise ValueError("planner delegation action无效")
        for name in (
            "recipient_role",
            "task_instruction",
            "acceptance_summary",
        ):
            if not isinstance(value[name], str) or not value[name].strip():
                raise ValueError(f"{name}不能为空")
        capabilities = value["required_capabilities"]
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item.strip() for item in capabilities
        ):
            raise ValueError("required_capabilities无效")
        return _PlannerDelegation(
            recipient_role=value["recipient_role"].strip(),
            task_instruction=value["task_instruction"].strip(),
            required_capabilities=tuple(capabilities),
            acceptance_summary=value["acceptance_summary"].strip(),
        )

    @staticmethod
    def _planner_prompt(user_prompt: str) -> str:
        return json.dumps({
            "instruction": (
                "把用户任务拆成一个最小委派。只能返回planner-delegation/v1；"
                "指定recipient_role，不能指定具体Agent。"
            ),
            "allowed_action": "delegate_task",
            "user_task": user_prompt,
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _recipient_prompt(
        request: ProductTaskRequest,
        delegation: _PlannerDelegation,
        message: Message,
    ) -> str:
        return json.dumps({
            "schema_version": "product-recipient-context/v1",
            "task_id": request.task_id,
            "task_goal": request.prompt,
            "assigned_instruction": delegation.task_instruction,
            "acceptance_summary": delegation.acceptance_summary,
            "trigger_message": {
                "message_id": message.message_id,
                "content": message.body,
            },
            "constraint": "返回候选结果；最终是否通过由Runtime Validator决定。",
        }, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _state(
        cls,
        request: ProductTaskRequest,
        *,
        invocation_id: str,
        content: str,
    ) -> AgentExecutionStateEnvelope:
        snapshot_hash = sha256(content.encode("utf-8")).hexdigest()
        permission_hash = canonical_digest({
            "permission": request.permission.value,
            "scope_id": request.scope_id,
        })
        return AgentExecutionStateEnvelope(
            scope_id=request.scope_id,
            task_ref=cls._ref(
                request.scope_id,
                "core:task",
                request.task_id,
            ),
            snapshot_ref=ScopedSnapshotRef(
                cls._ref(
                    request.scope_id,
                    "core:task_snapshot",
                    f"{invocation_id}-task",
                ),
                snapshot_hash,
            ),
            permission_snapshot_ref=ScopedSnapshotRef(
                cls._ref(
                    request.scope_id,
                    "core:permission_snapshot",
                    f"{invocation_id}-permission",
                ),
                permission_hash,
            ),
            artifact_refs=(),
            permission=request.permission,
        )

    @staticmethod
    def _ids(request: ProductTaskRequest) -> Mapping[str, str]:
        prefix = sha256(
            f"{request.scope_id}\0{request.task_id}".encode("utf-8")
        ).hexdigest()[:24]
        return MappingProxyType({
            "thread_id": f"product-thread-{prefix}",
            "thread_event_id": f"product-thread-event-{prefix}",
            "thread_idempotency_key": f"product-thread-create-{prefix}",
            "trace_id": f"product-trace-{prefix}",
            "correlation_id": f"product-task-{prefix}",
            "turn_id": f"product-turn-{prefix}",
            "planner_invocation_id": f"product-planner-{prefix}",
            "recipient_invocation_id": f"product-recipient-{prefix}",
            "requirement_id": f"product-requirement-{prefix}",
            "assignment_id": f"product-assignment-{prefix}",
            "message_id": f"product-message-{prefix}",
        })

    @staticmethod
    def _request_digest(request: ProductTaskRequest) -> str:
        return canonical_digest({
            "schema_version": "product-task-request/v1",
            "scope_id": request.scope_id,
            "task_id": request.task_id,
            "prompt": request.prompt,
            "permission": request.permission.value,
            "timeout_seconds": request.timeout_seconds,
        })

    @staticmethod
    def _ref(scope_id: str, entity_type: str, entity_id: str) -> ScopedRef:
        return ScopedRef(scope_id, entity_type, entity_id, 1)

    @classmethod
    def _agent_ref(cls, scope_id: str, agent_id: str) -> ScopedRef:
        return cls._ref(scope_id, "core:agent_instance", agent_id)

    @classmethod
    def _role_ref(cls, scope_id: str, role_id: str) -> ScopedRef:
        return cls._ref(
            scope_id,
            "core:agent_role",
            f"collaboration:{role_id}",
        )

    @staticmethod
    def _result(
        request: ProductTaskRequest,
        ids: Mapping[str, str],
        status: ProductTaskStatus,
        **fields: object,
    ) -> ProductTaskResult:
        return ProductTaskResult(
            task_id=request.task_id,
            request_digest=LocalProductTaskService._request_digest(request),
            thread_id=ids["thread_id"],
            status=status,
            planner_invocation_id=ids["planner_invocation_id"],
            recipient_invocation_id=ids["recipient_invocation_id"],
            **fields,
        )


__all__ = [
    "LocalProductTaskService",
    "ProductAgentConfig",
    "ProductTaskConflictError",
    "ProductTaskRequest",
    "ProductTaskResult",
    "ProductTaskStatus",
]
