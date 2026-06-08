"""Serialization helpers for web views."""

from __future__ import annotations

import dataclasses
from typing import Any


def view_to_dict(view: Any) -> Any:
    """Convert a frozen View dataclass to a plain dict.

    Recursively converts nested dataclasses and tuples to dicts/lists.
    Non-dataclass values are returned as-is.
    """
    if not dataclasses.is_dataclass(view):
        return view

    result: dict[str, Any] = {}
    for f in dataclasses.fields(view):
        val = getattr(view, f.name)
        if dataclasses.is_dataclass(val):
            result[f.name] = view_to_dict(val)
        elif isinstance(val, tuple):
            result[f.name] = [
                view_to_dict(item) if dataclasses.is_dataclass(item) else item for item in val
            ]
        elif isinstance(val, dict):
            result[f.name] = dict(val)
        else:
            result[f.name] = val
    return result


# Tailwind CSS classes for each state
_STATE_CSS: dict[str, str] = {
    "draft": "bg-gray-700 text-gray-300",
    "planning": "bg-gray-700 text-gray-300",
    "plan_review": "bg-yellow-900 text-yellow-300",
    "review": "bg-yellow-900 text-yellow-300",
    "await_audit": "bg-yellow-900 text-yellow-300",
    "plan_approved": "bg-cyan-900 text-cyan-300",
    "in_progress": "bg-blue-900 text-blue-300",
    "testing": "bg-purple-900 text-purple-300",
    "integrating": "bg-purple-900 text-purple-300",
    "mutation": "bg-purple-900 text-purple-300",
    "rework": "bg-red-900 text-red-300",
    "pr_ready": "bg-green-900 text-green-300",
    "merged": "bg-green-900 text-green-300",
    "accepted": "bg-emerald-900 text-emerald-200 font-semibold",
    "failed": "bg-red-900 text-red-200 font-semibold",
}


def state_css(state: str) -> str:
    """Return Tailwind classes for a state badge."""
    return _STATE_CSS.get(state, "bg-gray-700 text-gray-300")
