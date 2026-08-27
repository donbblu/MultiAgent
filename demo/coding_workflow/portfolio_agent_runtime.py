from __future__ import annotations

import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from .agent_runtime import AgentLaneRuntime, AgentManager, MailboxManager
from .artifacts import ArtifactStore
from .coding_ablation import (
    AblationBudgetLedger,
    AblationStageAudit,
    AblationStrategy,
    AblationStrategyProfile,
    CodingAblationReport,
    CodingAblationRunner,
)
from .coding_evaluation import FixedCodingSuite, FixedCodingTask
from .harness.registry import WorkerRegistry
from .runtime_domain import (
    Message,
    RuntimeActorType,
    RuntimeEvent,
    ScopedRef,
    Thread,
)
from .runtime_persistence import (
    OutboxPolicy,
    RuntimeSQLiteConfig,
    SQLiteRuntimeDatabase,
    SQLiteThreadEventStore,
    ThreadEventMutation,
)


_SCOPE_ID = "portfolio-demo"


@dataclass(frozen=True)
class _StageRoute:
    public_role: str
    handoff_kind: str
    sender_role: str | None


_STAGE_ROUTES = {
    "plan": _StageRoute(
        "Planner", "core:task_assignment", None,
    ),
    "implement": _StageRoute(
        "Developer", "core:plan_handoff", "planner",
    ),
    "diagnose": _StageRoute(
        "Tester",
        "core:validation_diagnosis_handoff",
        "implementer",
    ),
    "fix": _StageRoute(
        "Fixer", "core:repair_handoff", "tester",
    ),
}


class _TickClock:
    def __init__(self) -> None:
        self._instant = datetime.now(timezone.utc)
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            value = self._instant
            self._instant += timedelta(microseconds=1)
        return value.isoformat()


@dataclass(frozen=True)
class PortfolioAgentRun:
    report: CodingAblationReport
    runtime: Mapping[str, object]


@dataclass
class _AgentEvidence:
    role: str
    thread_id: str
    agent_instance_id: str
    agent_session_id: str
    lifecycle: list[str] = field(default_factory=lambda: ["created"])
    state_version: int = 0
    state: dict[str, object] = field(default_factory=dict)
    consumed_sequences: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class _StageMessageEvidence:
    task_id: str
    strategy: str
    stage: str
    kind: str
    message_id: str
    thread_id: str
    parent_message_id: str | None
    causation_message_id: str | None
    sender_agent_id: str
    recipient_agent_id: str
    recipient_role: str
    recipient_session_id: str
    mailbox_sequence: int
    input_artifact_refs: tuple[str, ...]
    output_artifact_ref: str
    consumed: bool
    lane_thread: str

    @property
    def is_handoff(self) -> bool:
        return self.sender_agent_id != self.recipient_agent_id

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": "portfolio-agent-handoff/v1",
            "task_id": self.task_id,
            "strategy": self.strategy,
            "stage": self.stage,
            "kind": self.kind,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "parent_message_id": self.parent_message_id,
            "causation_message_id": self.causation_message_id,
            "sender_agent_id": self.sender_agent_id,
            "recipient_agent_id": self.recipient_agent_id,
            "recipient_role": self.recipient_role,
            "recipient_session_id": self.recipient_session_id,
            "mailbox_sequence": self.mailbox_sequence,
            "input_artifact_refs": list(self.input_artifact_refs),
            "output_artifact_ref": self.output_artifact_ref,
            "consumed": self.consumed,
            "lane_thread": self.lane_thread,
            "is_handoff": self.is_handoff,
        }


@dataclass
class _TrialContext:
    task_id: str
    strategy: AblationStrategy
    thread_id: str
    turn_ref: ScopedRef
    agents: dict[str, _AgentEvidence]
    next_message_sequence: int = 1
    previous_handoff_ref: ScopedRef | None = None
    stage_messages: list[_StageMessageEvidence] = field(default_factory=list)


class PortfolioAgentAblationRunner(CodingAblationRunner):
    """Run the fixed ablation through durable per-trial Agent mailboxes."""

    def __init__(
        self,
        suite: FixedCodingSuite,
        workers: WorkerRegistry,
        *,
        database_path: Path,
        trusted_local_execution: bool,
        max_parallel_trials: int = 3,
    ) -> None:
        super().__init__(
            suite,
            workers,
            trusted_local_execution=trusted_local_execution,
        )
        if (
            not isinstance(max_parallel_trials, int)
            or isinstance(max_parallel_trials, bool)
            or max_parallel_trials < 1
        ):
            raise ValueError("max_parallel_trials 必须是大于 0 的整数")
        self._database_path = Path(database_path).resolve()
        self._database = SQLiteRuntimeDatabase(
            RuntimeSQLiteConfig(self._database_path),
            outbox_policy=OutboxPolicy(
                policy_version="outbox-policy/portfolio-agent-runtime-v1",
                destination="core:runtime_events",
                expected_sink_id="core:portfolio-agent-runtime-sink",
                claim_ttl_ms=60_000,
                batch_limit=10,
                retry_delays_ms=(1_000, 5_000),
            ),
        )
        self._database.initialize()
        self._clock = _TickClock()
        self._agents = AgentManager(self._database, clock=self._clock)
        self._mailbox = MailboxManager(self._database, clock=self._clock)
        self._lanes = AgentLaneRuntime(
            self._mailbox,
            max_workers=max(4, max_parallel_trials),
        )
        self._thread_store = SQLiteThreadEventStore(self._database)
        self._max_parallel_trials = max_parallel_trials
        self._run_id = uuid4().hex
        self._local = threading.local()
        self._contexts: list[_TrialContext] = []
        self._contexts_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._active_agents = 0
        self._max_active_agents = 0
        parallel_participants = min(
            max_parallel_trials,
            len(self.suite.tasks) * len(self.profiles),
        )
        self._startup_barrier = (
            threading.Barrier(parallel_participants)
            if parallel_participants > 1
            else None
        )
        self._barrier_tickets = parallel_participants

    def run(self) -> PortfolioAgentRun:
        started_at = self._clock()
        ordered = [
            (task_index, profile_index, task, profile)
            for task_index, task in enumerate(self.suite.tasks)
            for profile_index, profile in enumerate(self.profiles)
        ]
        completed: dict[tuple[int, int], object] = {}
        try:
            with tempfile.TemporaryDirectory(prefix="core-agent-ablation-") as temp:
                root = Path(temp)
                with ThreadPoolExecutor(
                    max_workers=self._max_parallel_trials,
                    thread_name_prefix="portfolio-trial",
                ) as executor:
                    futures = {
                        executor.submit(self.run_trial, task, profile, root): (
                            task_index,
                            profile_index,
                        )
                        for task_index, profile_index, task, profile in ordered
                    }
                    for future in as_completed(futures):
                        completed[futures[future]] = future.result()
            report = CodingAblationReport(
                self.suite.suite_id,
                self.suite.manifest_sha256,
                self.profiles[0].budget.digest,
                {
                    item.strategy.value: item.digest for item in self.profiles
                },
                started_at,
                self._clock(),
                tuple(completed[(task_index, profile_index)] for (
                    task_index, profile_index, _, _
                ) in ordered),
                dry_run=not self.allow_model_usage,
            )
            self._database.verify_integrity()
            return PortfolioAgentRun(
                report=report,
                runtime=MappingProxyType(self._runtime_evidence()),
            )
        finally:
            self._lanes.shutdown()

    def run_trial(
        self,
        task: FixedCodingTask,
        profile: AblationStrategyProfile,
        root: Path,
    ):
        context = self._create_trial_context(task, profile)
        self._local.context = context
        try:
            return super().run_trial(task, profile, root)
        finally:
            self._close_context(context)
            with self._contexts_lock:
                self._contexts.append(context)
            del self._local.context

    def _call_stage(
        self,
        task: FixedCodingTask,
        profile: AblationStrategyProfile,
        stage_id: str,
        available: Mapping[str, str],
        artifacts: ArtifactStore,
        principals: dict[str, str],
        audits: list[AblationStageAudit],
        ledger: AblationBudgetLedger,
    ) -> str:
        context = self._required_context()
        stage = profile.stage(stage_id)
        route = _STAGE_ROUTES[stage_id]
        agent = context.agents[stage.role]
        visible_refs = tuple(
            reference
            for reference in available.values()
            if artifacts.get(reference).kind in stage.visible_kinds
        )
        bootstrap = self._message(
            context,
            sender=agent,
            recipient=agent,
            kind="core:context_bootstrap",
            body={
                "contract": "portfolio-agent-handoff/v1",
                "task_id": task.task_id,
                "strategy": profile.strategy.value,
                "stage": stage_id,
                "purpose": "load durable stage context before work",
            },
            artifact_refs=(),
            parent_ref=None,
            causation_ref=context.turn_ref,
        )
        self._send(bootstrap, agent)

        sender = context.agents.get(route.sender_role, agent)
        parent_ref = context.previous_handoff_ref
        kind = (
            route.handoff_kind
            if sender is not agent
            else "core:task_assignment"
        )
        work = self._message(
            context,
            sender=sender,
            recipient=agent,
            kind=kind,
            body={
                "contract": "portfolio-agent-handoff/v1",
                "task_id": task.task_id,
                "strategy": profile.strategy.value,
                "stage": stage_id,
                "recipient_role": agent.role,
                "input_artifact_refs": visible_refs,
            },
            artifact_refs=visible_refs,
            parent_ref=parent_ref,
            causation_ref=parent_ref or context.turn_ref,
        )
        _, queued_work = self._send(work, agent)
        self._record_sent(sender, work.message_id)

        output: list[str] = []
        consumed_work: list[object] = []
        lane_threads: list[str] = []

        def handle(delivery) -> None:
            agent.consumed_sequences.append(delivery.mailbox_sequence)
            self._record_received(agent, delivery.message.message_id)
            if delivery.message.message_id != work.message_id:
                return
            consumed_work.append(delivery)
            lane_threads.append(threading.current_thread().name)
            self._enter_agent_work()
            try:
                self._wait_for_parallel_start()
                output.append(super(
                    PortfolioAgentAblationRunner,
                    self,
                )._call_stage(
                    task,
                    profile,
                    stage_id,
                    available,
                    artifacts,
                    principals,
                    audits,
                    ledger,
                ))
            finally:
                self._leave_agent_work()

        processed = self._lanes.schedule(
            scope_id=_SCOPE_ID,
            thread_id=context.thread_id,
            agent_instance_id=agent.agent_instance_id,
            agent_session_id=agent.agent_session_id,
            handler=handle,
        ).result()
        if (
            processed != 2
            or len(output) != 1
            or len(consumed_work) != 1
            or len(lane_threads) != 1
        ):
            raise RuntimeError(
                "Agent lane 未完整消费bootstrap与stage work"
            )
        output_ref = output[0]
        self._record_artifact(agent, output_ref)
        consumed = consumed_work[0]
        context.stage_messages.append(_StageMessageEvidence(
            task_id=task.task_id,
            strategy=profile.strategy.value,
            stage=stage_id,
            kind=work.kind,
            message_id=work.message_id,
            thread_id=context.thread_id,
            parent_message_id=(
                None if work.parent_ref is None else work.parent_ref.entity_id
            ),
            causation_message_id=(
                work.causation_ref.entity_id
                if work.causation_ref is not None
                and work.causation_ref.entity_type == "core:message"
                else None
            ),
            sender_agent_id=sender.agent_instance_id,
            recipient_agent_id=agent.agent_instance_id,
            recipient_role=agent.role,
            recipient_session_id=agent.agent_session_id,
            mailbox_sequence=consumed.mailbox_sequence,
            input_artifact_refs=visible_refs,
            output_artifact_ref=output_ref,
            consumed=consumed.consumed,
            lane_thread=lane_threads[0],
        ))
        if queued_work.mailbox_sequence != consumed.mailbox_sequence:
            raise RuntimeError("Mailbox enqueue/consume sequence 不一致")
        context.previous_handoff_ref = work.reference
        return output_ref

    def _create_trial_context(
        self,
        task: FixedCodingTask,
        profile: AblationStrategyProfile,
    ) -> _TrialContext:
        suffix = f"{task.task_id}-{profile.strategy.value}"
        thread_id = f"portfolio-{self._run_id}-{suffix}"
        principal = ScopedRef(
            _SCOPE_ID,
            "core:runtime_principal",
            "portfolio-demo-runtime",
            1,
        )
        created_at = self._clock()
        thread = Thread(
            thread_id=thread_id,
            scope_id=_SCOPE_ID,
            title=suffix,
            participant_refs=(principal,),
            created_at=created_at,
            updated_at=created_at,
        )
        event = RuntimeEvent(
            scope_id=_SCOPE_ID,
            event_id=f"event-{thread_id}",
            event_type="core:thread_created",
            aggregate_ref=thread.reference,
            aggregate_version=1,
            sequence_no=1,
            trace_id=f"trace-{thread_id}",
            correlation_id=f"correlation-{thread_id}",
            actor_type=RuntimeActorType.RUNTIME,
            actor_ref=principal,
            idempotency_key=f"create-{thread_id}",
            occurred_at=created_at,
            recorded_at=created_at,
            thread_ref=thread.reference,
            payload={"state": "open"},
        )
        with self._database.unit_of_work() as uow:
            self._thread_store.apply(uow, ThreadEventMutation(0, thread, event))
            uow.commit()

        agents: dict[str, _AgentEvidence] = {}
        for stage in profile.stages:
            route = _STAGE_ROUTES[stage.stage_id]
            role = route.public_role
            agent_id = f"agent-{self._run_id}-{suffix}-{stage.role}"
            session_id = f"session-{self._run_id}-{suffix}-{stage.role}"
            record = self._agents.create_agent(
                agent_instance_id=agent_id,
                agent_session_id=session_id,
                scope_id=_SCOPE_ID,
                thread_ref=thread.reference,
                profile_ref=ScopedRef(
                    _SCOPE_ID,
                    "core:agent_profile",
                    f"portfolio-{stage.role}",
                    1,
                ),
                principal_id=f"principal-{stage.role}",
                created_at=self._clock(),
            )
            evidence = _AgentEvidence(
                role=role,
                thread_id=thread_id,
                agent_instance_id=agent_id,
                agent_session_id=session_id,
                state={
                    "goal": task.objective,
                    "steps": [stage.stage_id],
                    "received_message_refs": [],
                    "sent_message_refs": [],
                    "artifact_refs": [],
                },
            )
            self._write_state(evidence)
            paused = self._agents.pause_agent(
                scope_id=_SCOPE_ID,
                thread_id=thread_id,
                agent_instance_id=agent_id,
                agent_session_id=session_id,
                expected_session_version=record.session.version,
                updated_at=self._clock(),
            )
            evidence.lifecycle.append("paused")
            self._agents.resume_agent(
                scope_id=_SCOPE_ID,
                thread_id=thread_id,
                agent_instance_id=agent_id,
                agent_session_id=session_id,
                expected_session_version=paused.version,
                updated_at=self._clock(),
            )
            evidence.lifecycle.append("resumed")
            agents[stage.role] = evidence
        return _TrialContext(
            task_id=task.task_id,
            strategy=profile.strategy,
            thread_id=thread_id,
            turn_ref=ScopedRef(
                _SCOPE_ID,
                "core:turn",
                f"turn-{self._run_id}-{suffix}",
                1,
            ),
            agents=agents,
        )

    def _close_context(self, context: _TrialContext) -> None:
        for agent in context.agents.values():
            record = self._agents.get_agent(
                _SCOPE_ID,
                context.thread_id,
                agent.agent_instance_id,
            )
            if record is None or record.session.state.value == "closed":
                continue
            self._agents.close_agent(
                scope_id=_SCOPE_ID,
                thread_id=context.thread_id,
                agent_instance_id=agent.agent_instance_id,
                agent_session_id=agent.agent_session_id,
                expected_session_version=record.session.version,
                updated_at=self._clock(),
            )
            agent.lifecycle.append("closed")

    def _message(
        self,
        context: _TrialContext,
        *,
        sender: _AgentEvidence,
        recipient: _AgentEvidence,
        kind: str,
        body: Mapping[str, object],
        artifact_refs: tuple[str, ...],
        parent_ref: ScopedRef | None,
        causation_ref: ScopedRef,
    ) -> Message:
        sequence = context.next_message_sequence
        context.next_message_sequence += 1
        return Message(
            message_id=f"message-{self._run_id}-{uuid4().hex}",
            scope_id=_SCOPE_ID,
            thread_ref=ScopedRef(
                _SCOPE_ID,
                "core:thread",
                context.thread_id,
                1,
            ),
            turn_ref=context.turn_ref,
            sequence=sequence,
            sender_ref=self._agent_ref(sender),
            recipient_refs=(self._agent_ref(recipient),),
            kind=kind,
            body=json.dumps(dict(body), sort_keys=True),
            artifact_refs=tuple(
                ScopedRef(_SCOPE_ID, "core:artifact", reference, 1)
                for reference in artifact_refs
            ),
            parent_ref=parent_ref,
            causation_ref=causation_ref,
            created_at=self._clock(),
        )

    def _send(self, message: Message, recipient: _AgentEvidence):
        return self._mailbox.send_message(
            message,
            recipient_agent_instance_id=recipient.agent_instance_id,
            recipient_agent_session_id=recipient.agent_session_id,
        )

    @staticmethod
    def _agent_ref(agent: _AgentEvidence) -> ScopedRef:
        return ScopedRef(
            _SCOPE_ID,
            "core:agent_instance",
            agent.agent_instance_id,
            1,
        )

    def _record_sent(
        self,
        agent: _AgentEvidence,
        message_id: str,
    ) -> None:
        sent = agent.state["sent_message_refs"]
        assert isinstance(sent, list)
        sent.append(message_id)
        self._write_state(agent)

    def _record_received(
        self,
        agent: _AgentEvidence,
        message_id: str,
    ) -> None:
        received = agent.state["received_message_refs"]
        assert isinstance(received, list)
        received.append(message_id)
        self._write_state(agent)

    def _record_artifact(
        self,
        agent: _AgentEvidence,
        artifact_ref: str,
    ) -> None:
        artifacts = agent.state["artifact_refs"]
        assert isinstance(artifacts, list)
        artifacts.append(artifact_ref)
        self._write_state(agent)

    def _write_state(self, agent: _AgentEvidence) -> None:
        snapshot = self._agents.write_private_state(
            scope_id=_SCOPE_ID,
            thread_id=agent.thread_id,
            agent_instance_id=agent.agent_instance_id,
            agent_session_id=agent.agent_session_id,
            value=agent.state,
            expected_version=agent.state_version,
            updated_at=self._clock(),
        )
        agent.state_version = snapshot.version

    def _enter_agent_work(self) -> None:
        with self._activity_lock:
            self._active_agents += 1
            self._max_active_agents = max(
                self._max_active_agents,
                self._active_agents,
            )

    def _leave_agent_work(self) -> None:
        with self._activity_lock:
            self._active_agents -= 1

    def _wait_for_parallel_start(self) -> None:
        barrier = None
        with self._activity_lock:
            if self._barrier_tickets > 0:
                self._barrier_tickets -= 1
                barrier = self._startup_barrier
        if barrier is not None:
            barrier.wait(timeout=10)

    def _required_context(self) -> _TrialContext:
        context = getattr(self._local, "context", None)
        if not isinstance(context, _TrialContext):
            raise RuntimeError("Portfolio Agent stage 缺少Trial上下文")
        return context

    def _runtime_evidence(self) -> dict[str, object]:
        contexts = sorted(
            self._contexts,
            key=lambda item: (item.task_id, item.strategy.value),
        )
        agents: list[dict[str, object]] = []
        stage_evidence: list[_StageMessageEvidence] = []
        enqueued = 0
        consumed = 0
        fifo_observed = True
        states: dict[str, int] = {}
        for context in contexts:
            stage_evidence.extend(context.stage_messages)
            for evidence in context.agents.values():
                rows = self._mailbox.list_mailbox(
                    scope_id=_SCOPE_ID,
                    thread_id=context.thread_id,
                    agent_instance_id=evidence.agent_instance_id,
                    agent_session_id=evidence.agent_session_id,
                )
                sequences = [item.mailbox_sequence for item in rows]
                consumed_sequences = [
                    item.mailbox_sequence for item in rows if item.consumed
                ]
                enqueued += len(rows)
                consumed += len(consumed_sequences)
                fifo_observed = fifo_observed and (
                    sequences == [1, 2]
                    and consumed_sequences == [1, 2]
                    and evidence.consumed_sequences == [1, 2]
                )
                record = self._agents.get_agent(
                    _SCOPE_ID,
                    context.thread_id,
                    evidence.agent_instance_id,
                )
                if record is None:
                    raise RuntimeError("Agent evidence 指向缺失Agent")
                state = record.session.state.value
                states[state] = states.get(state, 0) + 1
                agents.append({
                    "task_id": context.task_id,
                    "strategy": context.strategy.value,
                    "thread_id": context.thread_id,
                    "role": evidence.role,
                    "agent_instance_id": evidence.agent_instance_id,
                    "agent_session_id": evidence.agent_session_id,
                    "session_state": state,
                    "session_version": record.session.version,
                    "lifecycle": list(evidence.lifecycle),
                    "mailbox_sequences": sequences,
                    "consumed_sequences": consumed_sequences,
                    "private_state_version": evidence.state_version,
                })
        stage_messages = [item.to_dict() for item in stage_evidence]
        handoffs = [
            item.to_dict() for item in stage_evidence if item.is_handoff
        ]
        return {
            "contract": "portfolio-agent-runtime/v1",
            "scope_id": _SCOPE_ID,
            "run_id": self._run_id,
            "database_path": str(self._database_path),
            "thread_count": len(contexts),
            "agent_count": len(agents),
            "stage_message_count": len(stage_messages),
            "handoff_count": len(handoffs),
            "agents": agents,
            "stage_messages": stage_messages,
            "handoffs": handoffs,
            "mailbox": {
                "enqueued": enqueued,
                "consumed": consumed,
                "all_consumed": enqueued == consumed,
                "consume_semantics": "receive-time cursor; no ack or redelivery",
            },
            "sessions": {
                "states": states,
                "all_closed": states == {"closed": len(agents)},
            },
            "lane_evidence": {
                "fifo_observed": fifo_observed,
                "per_agent_expected_sequences": [1, 2],
                "max_parallel_agents": self._max_active_agents,
                "shared_pool": True,
                "single_active_drain_per_agent": True,
            },
            "validator": {
                "owner": "runtime",
                "is_agent": False,
            },
            "limitations": [
                "Mailbox consumption commits at receive time; handler failures are not redelivered.",
                "Lane serialization is scoped to this single AgentLaneRuntime process instance.",
            ],
        }


__all__ = ["PortfolioAgentAblationRunner", "PortfolioAgentRun"]
