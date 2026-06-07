"""Core domain types for the orchestrator."""

from enum import Enum, unique


@unique
class Phase(Enum):
    """Project lifecycle phase (§7 of DEVELOPMENT_RULES)."""

    PROTOTYPE = "prototype"
    MVP = "mvp"
    PRODUCTION = "production"


@unique
class AuditVerdict(Enum):
    """Auditor decision on a PR (§6.3 of DEVELOPMENT_RULES)."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    REJECT = "reject"


@unique
class TaskState(Enum):
    """Task lifecycle state (§3 state machine)."""

    DRAFT = "draft"
    PLAN_REVIEW = "plan_review"
    PLAN_APPROVED = "plan_approved"
    IN_PROGRESS = "in_progress"
    TESTING = "testing"
    AWAIT_AUDIT = "await_audit"
    REWORK = "rework"
    MUTATION = "mutation"
    PR_READY = "pr_ready"
    MERGED = "merged"
    ACCEPTED = "accepted"
    FAILED = "failed"


@unique
class ModelId(Enum):
    """Supported Claude model identifiers."""

    SONNET = "claude-sonnet-4-5"
    OPUS = "claude-opus-4-6"
    HAIKU = "claude-haiku-4-5-20251001"
