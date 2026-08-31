from __future__ import annotations

from .runtime_domain import (
    AgentAvailability,
    AgentCandidate,
    AgentSessionState,
    AssignmentDecision,
    AssignmentRisk,
    CandidateEvaluation,
    RoleAssignment,
    RoleAssignmentPolicy,
    RoleRequirement,
    Message,
)
from .runtime_persistence import (
    AssignmentRecordResult,
    MailboxDelivery,
    MailboxSendResult,
    SQLiteMailboxStore,
    SQLiteRoleAssignmentStore,
    SQLiteRuntimeDatabase,
)


class RoleAssignmentScheduler:
    """Deterministic Runtime-owned Role-to-Agent selection."""

    def decide(
        self,
        *,
        assignment_id: str,
        requirement: RoleRequirement,
        candidates: tuple[AgentCandidate, ...],
        policy: RoleAssignmentPolicy,
        created_at: str,
    ) -> RoleAssignment:
        if not isinstance(requirement, RoleRequirement):
            raise TypeError("requirement 必须是 RoleRequirement")
        if not isinstance(policy, RoleAssignmentPolicy):
            raise TypeError("policy 必须是 RoleAssignmentPolicy")
        if not isinstance(candidates, (tuple, list)):
            raise TypeError("candidates 必须是数组")

        evaluations = tuple(
            self._evaluate(requirement, candidate)
            for candidate in candidates
        )
        eligible = sorted(
            (item for item in evaluations if item.eligible),
            key=lambda item: item.rank_key,
        )
        if not eligible:
            return self._assignment(
                assignment_id,
                requirement,
                evaluations,
                policy,
                AssignmentDecision.NEEDS_INPUT,
                "no_eligible_agent",
                created_at,
            )

        best = eligible[0]
        if best.availability is AgentAvailability.AVAILABLE:
            return self._assigned(
                assignment_id,
                requirement,
                evaluations,
                policy,
                best,
                "best_available",
                created_at,
            )

        must_wait_for_best = (
            requirement.risk is AssignmentRisk.HIGH
            or requirement.continuity_required
        )
        if not must_wait_for_best:
            fallback = next(
                (
                    item
                    for item in eligible[1:]
                    if item.availability is AgentAvailability.AVAILABLE
                ),
                None,
            )
            if fallback is not None:
                return self._assigned(
                    assignment_id,
                    requirement,
                    evaluations,
                    policy,
                    fallback,
                    "best_busy_eligible_fallback",
                    created_at,
                )

        if best.estimated_wait_seconds <= policy.max_wait_for_best_seconds:
            return self._assignment(
                assignment_id,
                requirement,
                evaluations,
                policy,
                AssignmentDecision.WAITING,
                "waiting_for_best",
                created_at,
            )
        return self._assignment(
            assignment_id,
            requirement,
            evaluations,
            policy,
            AssignmentDecision.NEEDS_INPUT,
            "best_wait_exceeds_policy",
            created_at,
        )

    @staticmethod
    def _evaluate(
        requirement: RoleRequirement,
        candidate: AgentCandidate,
    ) -> CandidateEvaluation:
        if not isinstance(candidate, AgentCandidate):
            raise TypeError("candidate 必须是 AgentCandidate")
        candidate.agent_instance_ref.assert_scope(requirement.scope_id, "candidate")
        rejections = []
        for capability in requirement.required_capabilities:
            if capability not in candidate.capabilities:
                rejections.append(f"missing_capability:{capability}")
        if candidate.session_state is not AgentSessionState.ACTIVE:
            rejections.append(f"session_{candidate.session_state.value}")
        if not candidate.permissions_granted:
            rejections.append("permission_denied")
        if not candidate.tools_available:
            rejections.append("tools_unavailable")
        if not candidate.context_available:
            rejections.append("context_unavailable")
        if not candidate.provider_healthy:
            rejections.append("provider_unhealthy")
        if not candidate.budget_available:
            rejections.append("budget_unavailable")
        return CandidateEvaluation(
            agent_instance_ref=candidate.agent_instance_ref,
            agent_session_ref=candidate.agent_session_ref,
            profile_ref=candidate.profile_ref,
            capabilities=candidate.capabilities,
            session_state=candidate.session_state,
            availability=candidate.availability,
            estimated_wait_seconds=candidate.estimated_wait_seconds,
            affinity_score=candidate.affinity_score,
            quality_score=candidate.quality_score,
            cost_rank=candidate.cost_rank,
            latency_rank=candidate.latency_rank,
            eligible=not rejections,
            rejection_codes=tuple(rejections),
        )

    @staticmethod
    def _assigned(
        assignment_id: str,
        requirement: RoleRequirement,
        evaluations: tuple[CandidateEvaluation, ...],
        policy: RoleAssignmentPolicy,
        selected: CandidateEvaluation,
        reason_code: str,
        created_at: str,
    ) -> RoleAssignment:
        return RoleAssignment(
            assignment_id=assignment_id,
            requirement=requirement,
            decision=AssignmentDecision.ASSIGNED,
            reason_code=reason_code,
            policy_version=policy.policy_version,
            candidate_evaluations=evaluations,
            selected_agent_instance_ref=selected.agent_instance_ref,
            selected_agent_session_ref=selected.agent_session_ref,
            selected_profile_ref=selected.profile_ref,
            created_at=created_at,
        )

    @staticmethod
    def _assignment(
        assignment_id: str,
        requirement: RoleRequirement,
        evaluations: tuple[CandidateEvaluation, ...],
        policy: RoleAssignmentPolicy,
        decision: AssignmentDecision,
        reason_code: str,
        created_at: str,
    ) -> RoleAssignment:
        return RoleAssignment(
            assignment_id=assignment_id,
            requirement=requirement,
            decision=decision,
            reason_code=reason_code,
            policy_version=policy.policy_version,
            candidate_evaluations=evaluations,
            created_at=created_at,
        )


class RoleAssignmentManager:
    """Transactional application API for immutable RoleAssignment records."""

    def __init__(self, database: SQLiteRuntimeDatabase) -> None:
        if not isinstance(database, SQLiteRuntimeDatabase):
            raise TypeError("database 必须是 SQLiteRuntimeDatabase")
        self._database = database
        self._store = SQLiteRoleAssignmentStore(database)
        self._mailbox_store = SQLiteMailboxStore(database)

    @property
    def store(self) -> SQLiteRoleAssignmentStore:
        return self._store

    def record(self, assignment: RoleAssignment) -> AssignmentRecordResult:
        with self._database.unit_of_work() as uow:
            result = self._store.record(uow, assignment)
            uow.commit()
        return result

    def get_assignment(
        self,
        *,
        scope_id: str,
        thread_id: str,
        assignment_id: str,
    ) -> RoleAssignment | None:
        return self._store.get_assignment(
            scope_id=scope_id,
            thread_id=thread_id,
            assignment_id=assignment_id,
        )

    def list_assignments(
        self,
        *,
        scope_id: str,
        thread_id: str,
    ) -> tuple[RoleAssignment, ...]:
        return self._store.list_assignments(
            scope_id=scope_id,
            thread_id=thread_id,
        )

    def record_and_enqueue(
        self,
        assignment: RoleAssignment,
        message: Message,
        *,
        enqueued_at: str,
    ) -> tuple[AssignmentRecordResult, MailboxSendResult, MailboxDelivery]:
        if not isinstance(assignment, RoleAssignment):
            raise TypeError("assignment 必须是 RoleAssignment")
        if assignment.decision is not AssignmentDecision.ASSIGNED:
            raise ValueError("只有 assigned 决策可以投递 Mailbox")
        if not isinstance(message, Message):
            raise TypeError("message 必须是 Message")
        selected = assignment.selected_agent_instance_ref
        selected_session = assignment.selected_agent_session_ref
        if (
            message.scope_id != assignment.scope_id
            or message.thread_id != assignment.thread_id
            or message.recipient_refs != (selected,)
        ):
            raise ValueError("Message recipient 必须与 RoleAssignment 完全一致")
        with self._database.unit_of_work() as uow:
            record_result = self._store.record(uow, assignment)
            send_result, delivery = self._mailbox_store.send(
                uow,
                message=message,
                recipient_agent_instance_id=selected.entity_id,
                recipient_agent_session_id=selected_session.entity_id,
                enqueued_at=enqueued_at,
            )
            uow.commit()
        return record_result, send_result, delivery


__all__ = [
    "AssignmentRecordResult",
    "RoleAssignmentManager",
    "RoleAssignmentScheduler",
]
