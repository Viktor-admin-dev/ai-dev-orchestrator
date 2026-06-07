"""Tests for TaskGraph — DAG operations, cycle detection, topological sort."""

from __future__ import annotations

import pytest

from orchestrator.graph import CycleError, TaskGraph, TaskNode

# ── Helpers ──


def _node(task_id: str, deps: set[str] | None = None, stage_id: str = "") -> TaskNode:
    return TaskNode(
        task_id=task_id,
        dependencies=frozenset(deps or set()),
        stage_id=stage_id,
    )


# ── Add / Remove / Get ──


class TestAddRemoveGet:
    def test_add_task(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        assert "A" in g.task_ids

    def test_add_duplicate_raises(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        with pytest.raises(ValueError, match="already exists"):
            g.add_task(_node("A"))

    def test_remove_task(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        removed = g.remove_task("A")
        assert removed.task_id == "A"
        assert "A" not in g.task_ids

    def test_remove_missing_raises(self) -> None:
        g = TaskGraph()
        with pytest.raises(KeyError, match="not found"):
            g.remove_task("X")

    def test_remove_depended_on_raises(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        with pytest.raises(ValueError, match="depended on"):
            g.remove_task("A")

    def test_get_task(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        assert g.get_task("A").task_id == "A"

    def test_get_missing_raises(self) -> None:
        g = TaskGraph()
        with pytest.raises(KeyError, match="not found"):
            g.get_task("X")

    def test_add_with_stage_id(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A", stage_id="S1"))
        assert g.get_task("A").stage_id == "S1"

    def test_task_ids_empty(self) -> None:
        g = TaskGraph()
        assert g.task_ids == set()


# ── Ready tasks ──


class TestReadyTasks:
    def test_empty_graph(self) -> None:
        g = TaskGraph()
        assert g.ready_tasks(set()) == set()

    def test_no_deps_all_ready(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B"))
        assert g.ready_tasks(set()) == {"A", "B"}

    def test_deps_block_readiness(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        assert g.ready_tasks(set()) == {"A"}

    def test_completed_deps_unblock(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        assert g.ready_tasks({"A"}) == {"B"}

    def test_completed_excluded(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        assert g.ready_tasks({"A", "B"}) == set()

    def test_diamond_graph(self) -> None:
        """A → B, A → C, B+C → D."""
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        g.add_task(_node("C", {"A"}))
        g.add_task(_node("D", {"B", "C"}))

        assert g.ready_tasks(set()) == {"A"}
        assert g.ready_tasks({"A"}) == {"B", "C"}
        assert g.ready_tasks({"A", "B"}) == {"C"}
        assert g.ready_tasks({"A", "B", "C"}) == {"D"}


# ── Topological sort ──


class TestTopologicalSort:
    def test_single_node(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        assert g.topological_sort() == ["A"]

    def test_chain(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        g.add_task(_node("C", {"B"}))
        result = g.topological_sort()
        assert result.index("A") < result.index("B") < result.index("C")

    def test_diamond(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        g.add_task(_node("C", {"A"}))
        g.add_task(_node("D", {"B", "C"}))
        result = g.topological_sort()
        assert result.index("A") < result.index("B")
        assert result.index("A") < result.index("C")
        assert result.index("B") < result.index("D")
        assert result.index("C") < result.index("D")

    def test_parallel_independent(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B"))
        g.add_task(_node("C"))
        result = g.topological_sort()
        assert set(result) == {"A", "B", "C"}


# ── Cycle detection ──


class TestCycleDetection:
    def test_self_loop(self) -> None:
        g = TaskGraph()
        with pytest.raises(CycleError):
            g.add_task(_node("A", {"A"}))

    def test_two_node_cycle(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        # Now try to add A depending on B — but A already exists
        # Instead, create a fresh graph with the cycle
        g2 = TaskGraph()
        g2.add_task(_node("A", {"B"}))
        with pytest.raises(CycleError):
            g2.add_task(_node("B", {"A"}))

    def test_three_node_cycle(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A", {"C"}))
        g.add_task(_node("B", {"A"}))
        with pytest.raises(CycleError):
            g.add_task(_node("C", {"B"}))

    def test_topological_sort_raises_on_cycle(self) -> None:
        """Direct internal cycle check via topological_sort."""
        g = TaskGraph()
        # Manually inject a cycle to test topological_sort directly
        g._nodes["A"] = _node("A", {"B"})
        g._nodes["B"] = _node("B", {"A"})
        with pytest.raises(CycleError, match="Cycle detected"):
            g.topological_sort()


# ── Validate ──


class TestValidate:
    def test_valid_graph(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A"))
        g.add_task(_node("B", {"A"}))
        assert g.validate() == []

    def test_dangling_reference(self) -> None:
        g = TaskGraph()
        # Manually add node with non-existent dependency
        g._nodes["B"] = _node("B", {"X"})
        issues = g.validate()
        assert any("unknown task X" in i for i in issues)

    def test_empty_graph_valid(self) -> None:
        g = TaskGraph()
        assert g.validate() == []


# ── Stage tasks ──


class TestStageTasks:
    def test_stage_tasks_filters(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A", stage_id="S1"))
        g.add_task(_node("B", stage_id="S1"))
        g.add_task(_node("C", stage_id="S2"))
        assert g.stage_tasks("S1") == {"A", "B"}
        assert g.stage_tasks("S2") == {"C"}

    def test_stage_tasks_empty(self) -> None:
        g = TaskGraph()
        assert g.stage_tasks("S1") == set()

    def test_is_stage_complete_true(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A", stage_id="S1"))
        g.add_task(_node("B", stage_id="S1"))
        assert g.is_stage_complete("S1", {"A", "B"}) is True

    def test_is_stage_complete_false(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A", stage_id="S1"))
        g.add_task(_node("B", stage_id="S1"))
        assert g.is_stage_complete("S1", {"A"}) is False

    def test_is_stage_complete_empty_stage(self) -> None:
        g = TaskGraph()
        assert g.is_stage_complete("S1", set()) is False

    def test_is_stage_complete_superset(self) -> None:
        g = TaskGraph()
        g.add_task(_node("A", stage_id="S1"))
        assert g.is_stage_complete("S1", {"A", "B", "C"}) is True
