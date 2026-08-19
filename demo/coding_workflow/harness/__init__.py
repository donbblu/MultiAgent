"""Coding Harness 的确定性控制面。"""

from .dispatcher import TaskDispatcher, TaskHandle, TaskStatus
from .executor import GraphExecutionResult, GraphWorker, TaskGraphExecutor, TaskRunRequest, TaskRunResult
from .lifecycle import (
    CancellationToken,
    LifecycleController,
    LifecycleEvent,
    LifecycleSnapshot,
    LifecycleState,
    LifecycleTransitionError,
    TaskCancelledError,
)
from .registry import WorkerRegistry
from .scheduler import GraphSnapshot, TaskGraphRuntime
from .scenario import (
    ConvergenceAction,
    ConvergenceDecision,
    ScenarioProfile,
    ScenarioRoundPlan,
    ScenarioRunState,
    ScenarioRuntime,
)
from .scenario_sqlite import SQLiteScenarioRunStore
from .task_graph import (
    GraphValidationError,
    ResourceConflict,
    TaskExecutionState,
    TaskGraph,
    TaskSpec,
)

__all__ = [
    "CancellationToken",
    "LifecycleController",
    "LifecycleEvent",
    "LifecycleSnapshot",
    "LifecycleState",
    "LifecycleTransitionError",
    "TaskCancelledError",
    "TaskDispatcher",
    "TaskHandle",
    "TaskStatus",
    "GraphExecutionResult",
    "GraphWorker",
    "TaskGraphExecutor",
    "TaskRunRequest",
    "TaskRunResult",
    "WorkerRegistry",
    "GraphSnapshot",
    "ConvergenceAction",
    "ConvergenceDecision",
    "ScenarioProfile",
    "ScenarioRoundPlan",
    "ScenarioRunState",
    "ScenarioRuntime",
    "SQLiteScenarioRunStore",
    "TaskGraphRuntime",
    "GraphValidationError",
    "ResourceConflict",
    "TaskExecutionState",
    "TaskGraph",
    "TaskSpec",
]
