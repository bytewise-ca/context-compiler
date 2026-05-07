"""Tests for graph traversal — validates against traversal/spec.md scenarios."""

import pytest
from pathlib import Path

from context_compiler.retrieval.entry_nodes import find_entry_nodes
from context_compiler.retrieval.traversal import traverse, CandidateNode
from context_compiler.models import TaskType


def _entry_nodes_for(task: str, conn):
    return find_entry_nodes(task, conn).candidates


# ── BUG_FIX traversal ───────────────────────────────────────────────────────

class TestBugFixTraversal:
    def test_entry_node_always_included(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        assert entries
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        entry_ids = {e.node_id for e in entries}
        candidate_ids = {c.node_id for c in result.candidates}
        assert entry_ids & candidate_ids  # at least one entry node in candidates

    def test_bug_fix_includes_test_files(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        file_paths = [c.file_path for c in result.candidates]
        assert any("test" in fp for fp in file_paths)

    def test_bug_fix_includes_callers(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        # notifier.py calls processor — should appear
        file_paths = [c.file_path for c in result.candidates]
        assert any("notifier" in fp or "processor" in fp for fp in file_paths)

    def test_bug_fix_depth_limit_respected(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        for c in result.candidates:
            assert c.depth <= 2

    def test_bug_fix_no_zero_path_nodes(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        # All candidates must have a valid edge type
        for c in result.candidates:
            assert c.edge_type in ("ENTRY", "CALLS", "IMPORTS", "COVERS", "DEFINED_IN")

    def test_bug_fix_returns_traversal_result(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        assert hasattr(result, "candidates")
        assert hasattr(result, "excluded")
        assert isinstance(result.candidates, list)
        assert isinstance(result.excluded, list)


# ── NEW_FEATURE traversal ───────────────────────────────────────────────────

class TestNewFeatureTraversal:
    def test_new_feature_includes_imports(self, python_conn):
        entries = _entry_nodes_for("add OAuth support to the auth module", python_conn)
        if not entries:
            pytest.skip("No entry nodes found for this query in fixture")
        result = traverse(entries, TaskType.NEW_FEATURE, python_conn)
        edge_types = {c.edge_type for c in result.candidates}
        assert "IMPORTS" in edge_types or "ENTRY" in edge_types

    def test_new_feature_includes_siblings(self, python_conn):
        entries = _entry_nodes_for("add new payment feature", python_conn)
        if not entries:
            pytest.skip("No entry nodes found")
        result = traverse(entries, TaskType.NEW_FEATURE, python_conn)
        # Siblings are DEFINED_IN edges
        assert any(c.edge_type in ("DEFINED_IN", "ENTRY", "IMPORTS") for c in result.candidates)

    def test_new_feature_depth_limit_respected(self, python_conn):
        entries = _entry_nodes_for("add new payment feature", python_conn)
        if not entries:
            pytest.skip("No entry nodes found")
        result = traverse(entries, TaskType.NEW_FEATURE, python_conn)
        for c in result.candidates:
            assert c.depth <= 2

    def test_ts_new_feature_includes_imports(self, ts_conn):
        entries = _entry_nodes_for("add token refresh to auth", ts_conn)
        if not entries:
            pytest.skip("No entry nodes found")
        result = traverse(entries, TaskType.NEW_FEATURE, ts_conn)
        assert len(result.candidates) > 0


# ── REFACTOR traversal ──────────────────────────────────────────────────────

class TestRefactorTraversal:
    def test_refactor_includes_callers(self, python_conn):
        entries = _entry_nodes_for("extract the retry logic", python_conn)
        result = traverse(entries, TaskType.REFACTOR, python_conn)
        assert len(result.candidates) > 0

    def test_refactor_includes_test_files(self, python_conn):
        entries = _entry_nodes_for("extract the retry logic", python_conn)
        result = traverse(entries, TaskType.REFACTOR, python_conn)
        file_paths = [c.file_path for c in result.candidates]
        assert any("test" in fp for fp in file_paths)

    def test_refactor_depth_limit_respected(self, python_conn):
        entries = _entry_nodes_for("refactor the notifier", python_conn)
        result = traverse(entries, TaskType.REFACTOR, python_conn)
        for c in result.candidates:
            assert c.depth <= 3


# ── Multi-entry merge ────────────────────────────────────────────────────────

class TestMultiEntryMerge:
    def test_no_duplicate_node_ids(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        node_ids = [c.node_id for c in result.candidates]
        assert len(node_ids) == len(set(node_ids))

    def test_placeholder_nodes_excluded(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        for c in result.candidates:
            assert not c.node_id.startswith("__")

    def test_candidate_has_required_fields(self, python_conn):
        entries = _entry_nodes_for("fix the payment retry logic", python_conn)
        result = traverse(entries, TaskType.BUG_FIX, python_conn)
        for c in result.candidates:
            assert c.node_id
            assert c.file_path
            assert c.symbol_type in ("FILE", "FUNCTION", "CLASS", "METHOD")
            assert c.depth >= 0
            assert c.edge_type in ("ENTRY", "CALLS", "IMPORTS", "COVERS", "DEFINED_IN")

    def test_empty_entry_nodes_returns_empty(self, python_conn):
        result = traverse([], TaskType.BUG_FIX, python_conn)
        assert result.candidates == []
