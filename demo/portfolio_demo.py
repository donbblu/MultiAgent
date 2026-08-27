from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from coding_workflow import (
    AblationStrategy,
    CodingAblationReport,
    FixedCodingSuite,
    build_scripted_ablation_registry,
)
from coding_workflow.portfolio_agent_runtime import (
    PortfolioAgentAblationRunner,
)


CLI_CONTRACT_ID = "portfolio-demo/v1"
REPORT_SCHEMA_VERSION = "portfolio-demo-report/v2"
DEMO_ID = "portfolio-demo"
SUITE_ID = "core-coding-eval-v1"
SUITE_MANIFEST_SHA256 = (
    "cea75c0ee1f8fafc4d4eebfabbe2ff8f18ee1f2624d3831e198cce984827ee91"
)
TASK_IDS = (
    "python-tax-rounding",
    "python-user-payload",
    "python-inventory-cli",
)
MAIN_TASK_ID = "python-inventory-cli"
MAIN_STRATEGY = AblationStrategy.TESTER_FIXER.value
REPORT_RELATIVE_PATH = "demo/.runs/portfolio-demo/report.json"
DEMO_ROOT = Path(__file__).resolve().parent
SUITE_PATH = DEMO_ROOT / "coding_eval" / "v1"
REPORT_PATH = DEMO_ROOT / ".runs" / "portfolio-demo" / "report.json"
RUNTIME_DB_PATH = DEMO_ROOT / ".runs" / "portfolio-demo" / "runtime.sqlite3"


EXPECTED_TRIAL = {
    AblationStrategy.SINGLE_AGENT.value: {
        "outcome": "failed",
        "initial_outcome": "failed",
        "delivered": False,
        "first_passed": False,
        "fix_attempted": False,
        "fix_succeeded": False,
        "fix_rounds": 0,
        "worker_calls": 1,
        "scripted_calls": 1,
    },
    AblationStrategy.PLANNER_DEVELOPER.value: {
        "outcome": "passed",
        "initial_outcome": "passed",
        "delivered": True,
        "first_passed": True,
        "fix_attempted": False,
        "fix_succeeded": False,
        "fix_rounds": 0,
        "worker_calls": 2,
        "scripted_calls": 2,
    },
    AblationStrategy.TESTER_FIXER.value: {
        "outcome": "passed",
        "initial_outcome": "failed",
        "delivered": True,
        "first_passed": False,
        "fix_attempted": True,
        "fix_succeeded": True,
        "fix_rounds": 1,
        "worker_calls": 4,
        "scripted_calls": 4,
    },
}

EXPECTED_STAGES = {
    AblationStrategy.SINGLE_AGENT.value: (
        ("implement", "implementer", "core:patch"),
    ),
    AblationStrategy.PLANNER_DEVELOPER.value: (
        ("plan", "planner", "core:plan"),
        ("implement", "implementer", "core:patch"),
    ),
    AblationStrategy.TESTER_FIXER.value: (
        ("plan", "planner", "core:plan"),
        ("implement", "implementer", "core:patch"),
        ("diagnose", "tester", "core:test_diagnosis"),
        ("fix", "fixer", "core:patch"),
    ),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed scripted/offline Multi-Agent Harness portfolio demo."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--trusted-local-execution",
        action="store_true",
        help=(
            "approve only the fixed suite's registered local Python validators"
        ),
    )
    args = parser.parse_args(argv)
    if not args.trusted_local_execution:
        parser.error(
            "the fixed demo runs local validators; explicitly provide "
            "--trusted-local-execution"
        )
    return args


def _value(value: object) -> object:
    return getattr(value, "value", value)


def _mismatch(
    scope: str,
    reason: str,
    *,
    expected: object,
    actual: object,
) -> dict[str, object]:
    return {
        "scope": scope,
        "reason": reason,
        "expected": expected,
        "actual": actual,
    }


def _actual_matrix(report: CodingAblationReport) -> list[dict[str, object]]:
    return [
        {
            "task_id": trial.task_id,
            "strategy": _value(trial.strategy),
            "outcome": _value(trial.outcome),
            "initial_outcome": _value(trial.initial_outcome),
            "delivered": trial.delivered,
            "first_passed": trial.first_passed,
            "fix_attempted": trial.fix_attempted,
            "fix_succeeded": trial.fix_succeeded,
            "fix_rounds": trial.fix_rounds,
            "worker_calls": trial.worker_calls,
            "scripted_calls": trial.scripted_calls,
            "model_calls": trial.model_calls,
            "unauthorized_attempts": trial.unauthorized_attempts,
            "human_interventions": trial.human_interventions,
            "validator_outcomes": dict(trial.validator_outcomes),
            "stages": [audit.stage_id for audit in trial.stage_audits],
        }
        for trial in report.trials
    ]


def evaluate_report(
    report: CodingAblationReport,
    suite: FixedCodingSuite,
    runtime: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    mismatches: list[dict[str, object]] = []
    suite_task_ids = tuple(task.task_id for task in suite.tasks)
    global_checks = (
        ("suite.id", SUITE_ID, suite.suite_id),
        ("suite.manifest", SUITE_MANIFEST_SHA256, suite.manifest_sha256),
        ("suite.tasks", TASK_IDS, suite_task_ids),
        ("report.suite_id", SUITE_ID, report.suite_id),
        (
            "report.suite_manifest",
            SUITE_MANIFEST_SHA256,
            report.suite_manifest_sha256,
        ),
        ("report.dry_run", True, report.dry_run),
        ("report.trial_count", 9, len(report.trials)),
    )
    for scope, expected, actual in global_checks:
        if actual != expected:
            mismatches.append(_mismatch(
                scope,
                "frozen contract mismatch",
                expected=expected,
                actual=actual,
            ))

    by_key: dict[tuple[str, str], list[object]] = {}
    for trial in report.trials:
        key = (trial.task_id, str(_value(trial.strategy)))
        by_key.setdefault(key, []).append(trial)
    expected_keys = {
        (task_id, strategy)
        for task_id in TASK_IDS
        for strategy in EXPECTED_TRIAL
    }
    actual_keys = set(by_key)
    for key in sorted(expected_keys - actual_keys):
        mismatches.append(_mismatch(
            f"trial:{key[0]}:{key[1]}",
            "required trial missing",
            expected="one complete trial",
            actual="missing",
        ))
    for key in sorted(actual_keys - expected_keys):
        mismatches.append(_mismatch(
            f"trial:{key[0]}:{key[1]}",
            "unexpected trial",
            expected="not present",
            actual=len(by_key[key]),
        ))

    for key in sorted(expected_keys & actual_keys):
        items = by_key[key]
        scope = f"trial:{key[0]}:{key[1]}"
        if len(items) != 1:
            mismatches.append(_mismatch(
                scope,
                "trial must be unique",
                expected=1,
                actual=len(items),
            ))
            continue
        trial = items[0]
        expected_trial = EXPECTED_TRIAL[key[1]]
        for field, expected in expected_trial.items():
            actual = _value(getattr(trial, field))
            if actual != expected:
                mismatches.append(_mismatch(
                    f"{scope}.{field}",
                    "fixed success matrix mismatch",
                    expected=expected,
                    actual=actual,
                ))

        actual_stages = tuple(
            (audit.stage_id, audit.role, audit.output_kind)
            for audit in trial.stage_audits
        )
        if actual_stages != EXPECTED_STAGES[key[1]]:
            mismatches.append(_mismatch(
                f"{scope}.stages",
                "public workflow stage mismatch",
                expected=EXPECTED_STAGES[key[1]],
                actual=actual_stages,
            ))
        usage_sources = tuple(
            str(_value(audit.usage_source)) for audit in trial.stage_audits
        )
        if any(source != "scripted" for source in usage_sources):
            mismatches.append(_mismatch(
                f"{scope}.usage_sources",
                "offline demo permits scripted worker usage only",
                expected="all scripted",
                actual=usage_sources,
            ))

        safety_checks = (
            ("model_calls", 0, trial.model_calls),
            ("unauthorized_attempts", 0, trial.unauthorized_attempts),
            ("human_interventions", 0, trial.human_interventions),
        )
        for field, expected, actual in safety_checks:
            if actual != expected:
                mismatches.append(_mismatch(
                    f"{scope}.{field}",
                    "offline safety invariant mismatch",
                    expected=expected,
                    actual=actual,
                ))

        validators = tuple(trial.validator_outcomes.values())
        if not validators or "unknown" in validators:
            mismatches.append(_mismatch(
                f"{scope}.validator_outcomes",
                "Validator result is missing or UNKNOWN",
                expected="non-empty outcomes without UNKNOWN",
                actual=validators,
            ))
        elif expected_trial["outcome"] == "passed" and any(
            outcome != "passed" for outcome in validators
        ):
            mismatches.append(_mismatch(
                f"{scope}.validator_outcomes",
                "a trial expected to pass has a failing Validator",
                expected="all passed",
                actual=validators,
            ))
        elif expected_trial["outcome"] == "failed" and "failed" not in validators:
            mismatches.append(_mismatch(
                f"{scope}.validator_outcomes",
                "the declared control failure lacks failing evidence",
                expected="at least one failed",
                actual=validators,
            ))

    totals = {
        "scripted_calls": sum(trial.scripted_calls for trial in report.trials),
        "external_model_calls": sum(
            trial.model_calls for trial in report.trials
        ),
        "unauthorized_attempts": sum(
            trial.unauthorized_attempts for trial in report.trials
        ),
        "human_interventions": sum(
            trial.human_interventions for trial in report.trials
        ),
    }
    for field, expected in (
        ("scripted_calls", 21),
        ("external_model_calls", 0),
        ("unauthorized_attempts", 0),
        ("human_interventions", 0),
    ):
        if totals[field] != expected:
            mismatches.append(_mismatch(
                f"summary.{field}",
                "fixed aggregate mismatch",
                expected=expected,
                actual=totals[field],
            ))
    runtime_checks = (
        ("agent_runtime.contract", "portfolio-agent-runtime/v1", runtime.get("contract")),
        ("agent_runtime.thread_count", 9, runtime.get("thread_count")),
        ("agent_runtime.agent_count", 21, runtime.get("agent_count")),
        ("agent_runtime.stage_message_count", 21, runtime.get("stage_message_count")),
        ("agent_runtime.handoff_count", 12, runtime.get("handoff_count")),
    )
    for scope, expected, actual in runtime_checks:
        if actual != expected:
            mismatches.append(_mismatch(
                scope,
                "real Agent Runtime evidence mismatch",
                expected=expected,
                actual=actual,
            ))
    mailbox = runtime.get("mailbox")
    sessions = runtime.get("sessions")
    lanes = runtime.get("lane_evidence")
    validator = runtime.get("validator")
    agents = runtime.get("agents")
    stage_messages = runtime.get("stage_messages")
    handoffs = runtime.get("handoffs")
    runtime_invariants = (
        ("agent_runtime.mailbox.enqueued", 42, mailbox.get("enqueued") if isinstance(mailbox, Mapping) else None),
        ("agent_runtime.mailbox.consumed", 42, mailbox.get("consumed") if isinstance(mailbox, Mapping) else None),
        ("agent_runtime.mailbox.all_consumed", True, mailbox.get("all_consumed") if isinstance(mailbox, Mapping) else None),
        ("agent_runtime.sessions.all_closed", True, sessions.get("all_closed") if isinstance(sessions, Mapping) else None),
        ("agent_runtime.lanes.fifo", True, lanes.get("fifo_observed") if isinstance(lanes, Mapping) else None),
        ("agent_runtime.validator.owner", "runtime", validator.get("owner") if isinstance(validator, Mapping) else None),
        ("agent_runtime.validator.is_agent", False, validator.get("is_agent") if isinstance(validator, Mapping) else None),
        ("agent_runtime.agents", 21, len(agents) if isinstance(agents, list) else None),
        ("agent_runtime.stage_messages", 21, len(stage_messages) if isinstance(stage_messages, list) else None),
        ("agent_runtime.handoffs", 12, len(handoffs) if isinstance(handoffs, list) else None),
    )
    for scope, expected, actual in runtime_invariants:
        if actual != expected:
            mismatches.append(_mismatch(
                scope,
                "Agent Runtime safety or lifecycle invariant mismatch",
                expected=expected,
                actual=actual,
            ))
    max_parallel = lanes.get("max_parallel_agents") if isinstance(lanes, Mapping) else None
    if not isinstance(max_parallel, int) or max_parallel < 2:
        mismatches.append(_mismatch(
            "agent_runtime.lanes.max_parallel_agents",
            "different Agents did not demonstrate shared-pool parallelism",
            expected=">= 2",
            actual=max_parallel,
        ))
    if isinstance(agents, list) and any(
        isinstance(agent, Mapping) and agent.get("role") == "Validator"
        for agent in agents
    ):
        mismatches.append(_mismatch(
            "agent_runtime.validator.separation",
            "Runtime-owned Validator must not be represented as an Agent",
            expected="no Validator Agent",
            actual="Validator Agent present",
        ))
    if isinstance(stage_messages, list):
        observed = {
            (item.get("task_id"), item.get("strategy"), item.get("stage"))
            for item in stage_messages if isinstance(item, Mapping)
        }
        expected = {
            (task_id, strategy, stage[0])
            for task_id in TASK_IDS
            for strategy, stages in EXPECTED_STAGES.items()
            for stage in stages
        }
        if observed != expected:
            mismatches.append(_mismatch(
                "agent_runtime.handoff_stage_coverage",
                "Handoff chain does not cover every real stage exactly",
                expected=sorted(expected),
                actual=sorted(observed),
            ))
    return _actual_matrix(report), mismatches


def _public_timeline(
    report: CodingAblationReport,
    runtime: Mapping[str, object],
) -> list[dict[str, object]]:
    main = next((
        trial for trial in report.trials
        if trial.task_id == MAIN_TASK_ID
        and _value(trial.strategy) == MAIN_STRATEGY
    ), None)
    if main is None:
        return []
    audits = {audit.stage_id: audit for audit in main.stage_audits}
    stage_messages = runtime.get("stage_messages")
    agents = runtime.get("agents")
    if not isinstance(stage_messages, list) or not isinstance(agents, list):
        return []
    by_stage = {
        str(item["stage"]): item
        for item in stage_messages
        if isinstance(item, Mapping)
        and item.get("task_id") == MAIN_TASK_ID
        and item.get("strategy") == MAIN_STRATEGY
    }
    agents_by_id = {
        str(item["agent_instance_id"]): item
        for item in agents if isinstance(item, Mapping)
    }
    main_thread_id = next(iter(by_stage.values()))["thread_id"] if by_stage else None
    events: list[dict[str, object]] = []
    for stage_id in ("plan", "implement"):
        handoff = by_stage.get(stage_id)
        if handoff is not None:
            audit = audits[stage_id]
            agent = agents_by_id[str(handoff["recipient_agent_id"])]
            events.append({
                "role": handoff["recipient_role"],
                "stage": stage_id,
                "artifact": audit.output_kind,
                "artifact_ref": handoff["output_artifact_ref"],
                "validator": None,
                "result": "completed",
                "agent_id": handoff["recipient_agent_id"],
                "session_id": handoff["recipient_session_id"],
                "message_id": handoff["message_id"],
                "thread_id": handoff["thread_id"],
                "session_state": agent["session_state"],
                "lifecycle": agent["lifecycle"],
                "is_handoff": handoff["is_handoff"],
            })
    events.append({
        "role": "Validator",
        "stage": "initial_validation",
        "artifact": "core:validator_feedback",
        "artifact_ref": None,
        "validator": "runtime-owned fixed suite",
        "result": str(_value(main.initial_outcome)),
        "agent_id": None,
        "session_id": None,
        "message_id": None,
        "thread_id": main_thread_id,
        "session_state": None,
        "lifecycle": [],
        "is_handoff": False,
    })
    for stage_id in ("diagnose", "fix"):
        handoff = by_stage.get(stage_id)
        if handoff is not None:
            audit = audits[stage_id]
            agent = agents_by_id[str(handoff["recipient_agent_id"])]
            events.append({
                "role": handoff["recipient_role"],
                "stage": stage_id,
                "artifact": audit.output_kind,
                "artifact_ref": handoff["output_artifact_ref"],
                "validator": None,
                "result": "completed",
                "agent_id": handoff["recipient_agent_id"],
                "session_id": handoff["recipient_session_id"],
                "message_id": handoff["message_id"],
                "thread_id": handoff["thread_id"],
                "session_state": agent["session_state"],
                "lifecycle": agent["lifecycle"],
                "is_handoff": handoff["is_handoff"],
            })
    events.append({
        "role": "Validator",
        "stage": "final_validation",
        "artifact": "core:validator_feedback",
        "artifact_ref": None,
        "validator": "runtime-owned fixed suite",
        "result": str(_value(main.outcome)),
        "agent_id": None,
        "session_id": None,
        "message_id": None,
        "thread_id": main_thread_id,
        "session_state": None,
        "lifecycle": [],
        "is_handoff": False,
    })
    return events


def build_portfolio_report(
    report: CodingAblationReport,
    suite: FixedCodingSuite,
    runtime: Mapping[str, object],
) -> dict[str, object]:
    actual_matrix, mismatches = evaluate_report(report, suite, runtime)
    trials = [dict(item.to_dict()) for item in report.trials]
    scripted_calls = sum(item.scripted_calls for item in report.trials)
    model_calls = sum(item.model_calls for item in report.trials)
    delivered = sum(item.delivered for item in report.trials)
    expected_failures = sum(
        item.strategy is AblationStrategy.SINGLE_AGENT
        and item.initial_outcome.value == "failed"
        and item.outcome.value == "failed"
        and not item.delivered
        for item in report.trials
    )
    repaired = sum(item.fix_succeeded for item in report.trials)
    status = "passed" if not mismatches else "failed"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "demo_id": DEMO_ID,
        "status": status,
        "mode": "offline_scripted",
        "suite": {
            "suite_id": suite.suite_id,
            "manifest_sha256": suite.manifest_sha256,
            "task_ids": [task.task_id for task in suite.tasks],
        },
        "execution": {
            "approval_source": "cli:--trusted-local-execution",
            "trusted_local_execution": True,
            "started_at": report.started_at,
            "completed_at": report.completed_at,
            "scripted_worker_calls": scripted_calls,
            "external_model_calls": model_calls,
            "temporary_trial_workspaces": True,
            "network_access": False,
            "real_provider": False,
            "web": False,
        },
        "workflow": {
            "main_task_id": MAIN_TASK_ID,
            "main_strategy": MAIN_STRATEGY,
            "artifact_kinds": [
                "core:coding_requirement",
                "core:source_snapshot",
                "core:plan",
                "core:patch",
                "core:validator_feedback",
                "core:test_diagnosis",
            ],
            "public_timeline": _public_timeline(report, runtime),
            "private_reasoning_included": False,
        },
        "summary": {
            "tasks": len(suite.tasks),
            "trials": len(report.trials),
            "delivered": delivered,
            "expected_failures": expected_failures,
            "repaired": repaired,
            "scripted_worker_calls": scripted_calls,
            "external_model_calls": model_calls,
        },
        "verification": {
            "expected_matrix": EXPECTED_TRIAL,
            "actual_matrix": actual_matrix,
            "mismatches": mismatches,
        },
        "trials": trials,
        "agent_runtime": dict(runtime),
        "output": {
            "repository_relative_path": REPORT_RELATIVE_PATH,
            "write_semantics": "temporary file plus atomic replacement",
            "repeat_run": "overwrites only this complete dedicated report",
        },
        "limitations": [
            (
                "Scripted workers use frozen fixtures/reference repair and prove "
                "Harness orchestration, permissions, Artifact flow, Validators, "
                "and the Tester/Fixer loop; they do not prove LLM quality."
            ),
            "Trial workspaces are temporary and are cleaned after the run.",
            "This demo has no Web UI, network access, or real model Provider.",
            "This portfolio run is not production certification.",
            (
                "Mailbox delivery uses receive-time consumption without ack, "
                "retry, or crash redelivery; lane serialization is single-process."
            ),
        ],
    }


def write_report_atomic(payload: Mapping[str, object], output_path: Path) -> Path:
    output = output_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def emit_public_output(payload: Mapping[str, object]) -> None:
    execution = payload["execution"]
    assert isinstance(execution, Mapping)
    print(
        "mode=scripted/offline network=false real_provider=false "
        f"external_model_calls={execution['external_model_calls']}"
    )
    runtime = payload["agent_runtime"]
    assert isinstance(runtime, Mapping)
    mailbox = runtime["mailbox"]
    sessions = runtime["sessions"]
    lanes = runtime["lane_evidence"]
    assert isinstance(mailbox, Mapping)
    assert isinstance(sessions, Mapping)
    assert isinstance(lanes, Mapping)
    closed = sessions["states"]
    assert isinstance(closed, Mapping)
    print(
        f"runtime scope={runtime['scope_id']} threads={runtime['thread_count']} "
        f"agents={runtime['agent_count']} sessions_closed={closed.get('closed', 0)} "
        f"mailbox_sent={mailbox['enqueued']} "
        f"mailbox_received={mailbox['consumed']} "
        f"stage_messages={runtime['stage_message_count']} "
        f"handoffs={runtime['handoff_count']} "
        f"fifo={str(bool(lanes['fifo_observed'])).lower()} "
        f"max_parallel_agents={lanes['max_parallel_agents']}"
    )
    workflow = payload["workflow"]
    assert isinstance(workflow, Mapping)
    timeline = workflow["public_timeline"]
    assert isinstance(timeline, list)
    for event in timeline:
        assert isinstance(event, Mapping)
        validator = event["validator"] or "none"
        artifact_ref = event["artifact_ref"] or "none"
        lifecycle = ">".join(event["lifecycle"]) or "runtime-owned"
        print(
            f"role={event['role']} stage={event['stage']} "
            f"Artifact={event['artifact']} ArtifactRef={artifact_ref} "
            f"Validator={validator} "
            f"result={event['result']} "
            f"thread_id={event['thread_id'] or 'none'} "
            f"agent_id={event['agent_id'] or 'none'} "
            f"session_id={event['session_id'] or 'none'} "
            f"session_state={event['session_state'] or 'runtime-owned'} "
            f"lifecycle={lifecycle} "
            f"message_id={event['message_id'] or 'none'} "
            f"handoff={str(bool(event['is_handoff'])).lower()}"
        )
    summary = payload["summary"]
    assert isinstance(summary, Mapping)
    print(
        f"status={payload['status']} tasks={summary['tasks']} "
        f"trials={summary['trials']} delivered={summary['delivered']} "
        f"expected_failures={summary['expected_failures']} "
        f"repaired={summary['repaired']} "
        f"external_model_calls={summary['external_model_calls']} "
        f"report={REPORT_RELATIVE_PATH}"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        suite = FixedCodingSuite.load(SUITE_PATH)
        registry, _ = build_scripted_ablation_registry(suite)
        RUNTIME_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        run = PortfolioAgentAblationRunner(
            suite,
            registry,
            database_path=RUNTIME_DB_PATH,
            trusted_local_execution=args.trusted_local_execution,
        ).run()
        payload = build_portfolio_report(run.report, suite, run.runtime)
        write_report_atomic(payload, REPORT_PATH)
    except Exception as exc:
        print(
            "portfolio demo failed before a complete portfolio report was "
            f"written: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 3
    emit_public_output(payload)
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
