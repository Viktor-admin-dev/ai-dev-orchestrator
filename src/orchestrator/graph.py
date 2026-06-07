"""Task dependency graph — pure DAG structure, no I/O."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


class CycleError(ValueError):
    """Raised when adding a dependency would create a cycle in the DAG."""


@dataclass(frozen=True)
class TaskNode:
    """Immutable node in the task dependency graph."""

    task_id: str
    dependencies: frozenset[str] = frozenset()
    stage_id: str = ""


class TaskGraph:
    """Directed acyclic graph of task dependencies.

    Pure data structure — no I/O, no executor calls.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}

    @property
    def task_ids(self) -> set[str]:
        """All task IDs in the graph."""
        return set(self._nodes)

    def add_task(self, node: TaskNode) -> None:
        """Add a task node to the graph.

        Raises ``CycleError`` if adding this node would create a cycle.
        Raises ``ValueError`` if task_id already exists.
        """
        if node.task_id in self._nodes:
            raise ValueError(f"Task {node.task_id} already exists")

        # Temporarily add to check for cycles
        self._nodes[node.task_id] = node
        try:
            self._check_cycles()
        except CycleError:
            del self._nodes[node.task_id]
            raise

    def remove_task(self, task_id: str) -> TaskNode:
        """Remove a task node from the graph.

        Raises ``KeyError`` if not found.
        Raises ``ValueError`` if other tasks depend on this one.
        """
        if task_id not in self._nodes:
            raise KeyError(f"Task {task_id} not found")

        # Check if any other task depends on this one
        dependents = [n.task_id for n in self._nodes.values() if task_id in n.dependencies]
        if dependents:
            raise ValueError(f"Cannot remove {task_id}: depended on by {dependents}")

        return self._nodes.pop(task_id)

    def get_task(self, task_id: str) -> TaskNode:
        """Get a task node by ID. Raises ``KeyError`` if not found."""
        if task_id not in self._nodes:
            raise KeyError(f"Task {task_id} not found")
        return self._nodes[task_id]

    def ready_tasks(self, completed: set[str]) -> set[str]:
        """Return task IDs whose dependencies are all in ``completed``.

        Already-completed tasks are excluded from the result.
        """
        ready: set[str] = set()
        for node in self._nodes.values():
            if node.task_id in completed:
                continue
            if node.dependencies <= completed:
                ready.add(node.task_id)
        return ready

    def topological_sort(self) -> list[str]:
        """Return task IDs in topological order (Kahn's algorithm).

        Raises ``CycleError`` if the graph has a cycle.
        """
        in_degree: dict[str, int] = {tid: 0 for tid in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep in in_degree:
                    in_degree[node.task_id] += 0  # dep exists
                    # node depends on dep, so node's in-degree is counted below

        # Recount properly: for each node, each dependency adds 1 to in-degree
        in_degree = {tid: 0 for tid in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep in self._nodes:
                    in_degree[node.task_id] += 1

        queue: deque[str] = deque(tid for tid, deg in in_degree.items() if deg == 0)
        result: list[str] = []

        while queue:
            tid = queue.popleft()
            result.append(tid)
            # Find tasks that depend on tid and decrement their in-degree
            for node in self._nodes.values():
                if tid in node.dependencies and node.task_id in in_degree:
                    in_degree[node.task_id] -= 1
                    if in_degree[node.task_id] == 0:
                        queue.append(node.task_id)

        if len(result) != len(self._nodes):
            raise CycleError("Cycle detected in task graph")

        return result

    def validate(self) -> list[str]:
        """Validate the graph, returning a list of issues.

        Checks for:
        - Dangling dependency references (deps not in graph)
        - Cycles
        """
        issues: list[str] = []

        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep not in self._nodes:
                    issues.append(f"Task {node.task_id} depends on unknown task {dep}")

        try:
            self.topological_sort()
        except CycleError as e:
            issues.append(str(e))

        return issues

    def stage_tasks(self, stage_id: str) -> set[str]:
        """Return task IDs belonging to a given stage."""
        return {node.task_id for node in self._nodes.values() if node.stage_id == stage_id}

    def is_stage_complete(self, stage_id: str, completed: set[str]) -> bool:
        """Check if all tasks in a stage are in the completed set.

        Returns False if the stage has no tasks.
        """
        tasks = self.stage_tasks(stage_id)
        if not tasks:
            return False
        return tasks <= completed

    def _check_cycles(self) -> None:
        """Raise ``CycleError`` if the graph contains a cycle."""
        self.topological_sort()
