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
class ModelId(Enum):
    """Supported Claude model identifiers."""

    SONNET = "claude-sonnet-4-5"
    OPUS = "claude-opus-4-6"
    HAIKU = "claude-haiku-4-5-20251001"
