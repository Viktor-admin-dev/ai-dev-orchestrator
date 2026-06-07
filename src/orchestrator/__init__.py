"""AI Dev Orchestrator — AI-контур разработки проектов."""

from orchestrator.config import ProjectConfig
from orchestrator.cost import BudgetExceededError, CostTracker
from orchestrator.evidence import EvidencePack, MutationResults, TestResults, parse_verdict
from orchestrator.executor import ExecutionResult, ExecutorAdapter
from orchestrator.graph import CycleError, TaskGraph, TaskNode
from orchestrator.loop import RunResult, run_loop
from orchestrator.panel import (
    CostReportView,
    PanelData,
    PendingAction,
    ProjectStatusView,
    StageStatusView,
    TaskLogView,
    TaskStatusView,
)
from orchestrator.project import ProjectOrchestrator
from orchestrator.runner import TaskRunner
from orchestrator.stage import InvalidStageTransitionError, StageContext, StageFSM, StageTransition
from orchestrator.state_machine import InvalidTransitionError, TaskContext, TaskCycleFSM, Transition
from orchestrator.store import init_db, load_orchestrator, save_snapshot
from orchestrator.types import AuditVerdict, ModelId, Phase, StageState, TaskState

__version__ = "0.1.0"

__all__ = [
    "AuditVerdict",
    "BudgetExceededError",
    "CostReportView",
    "CostTracker",
    "CycleError",
    "EvidencePack",
    "ExecutionResult",
    "ExecutorAdapter",
    "InvalidStageTransitionError",
    "InvalidTransitionError",
    "ModelId",
    "MutationResults",
    "PanelData",
    "PendingAction",
    "Phase",
    "ProjectConfig",
    "ProjectOrchestrator",
    "ProjectStatusView",
    "RunResult",
    "StageContext",
    "StageFSM",
    "StageState",
    "StageStatusView",
    "StageTransition",
    "TaskContext",
    "TaskCycleFSM",
    "TaskGraph",
    "TaskLogView",
    "TaskNode",
    "TaskRunner",
    "TaskState",
    "TaskStatusView",
    "TestResults",
    "Transition",
    "__version__",
    "init_db",
    "load_orchestrator",
    "parse_verdict",
    "run_loop",
    "save_snapshot",
]
