"""Tests for the MCP server — validates against mcp-server/spec.md scenarios."""

import os
import shutil
import kuzu
import pytest
from pathlib import Path

from context_compiler.indexer.indexer import index_repository
from context_compiler.indexer.graph import open_database


PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_repo"


@pytest.fixture
def indexed_repo(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(PYTHON_FIXTURE, repo)
    index_repository(repo, verbose=False)
    return repo


@pytest.fixture
def empty_repo(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    return repo


def _call_get_context(repo: Path, task: str, budget: int = 8000) -> dict:
    """Call get_context with the repo set via env var."""
    import importlib
    os.environ["CC_REPO_PATH"] = str(repo)
    # Re-import to pick up env var (module-level state)
    from context_compiler.server import mcp_server
    importlib.reload(mcp_server)
    return mcp_server.get_context(task=task, budget=budget)


def _call_refresh(repo: Path, changed_files: list[str]) -> dict:
    import importlib
    os.environ["CC_REPO_PATH"] = str(repo)
    from context_compiler.server import mcp_server
    importlib.reload(mcp_server)
    return mcp_server.refresh(changed_files=changed_files)


# ── Scenario: Successful get_context call ───────────────────────────────────

class TestGetContext:
    def test_returns_context_bundle_structure(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        assert "files" in result
        assert "rationale" in result
        assert "token_estimate" in result
        assert "tokens_saved" in result
        assert "task_type" in result
        assert "confidence" in result

    def test_files_and_rationale_same_length(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        assert len(result["files"]) == len(result["rationale"])

    def test_task_type_valid(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        assert result["task_type"] in ("BUG_FIX", "NEW_FEATURE", "REFACTOR")

    def test_confidence_in_range(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_token_estimate_non_negative(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        assert result["token_estimate"] >= 0

    def test_budget_respected(self, indexed_repo):
        budget = 500
        result = _call_get_context(indexed_repo, "fix the payment retry logic", budget=budget)
        assert result.get("token_estimate", 0) <= budget

    def test_no_files_outside_repo(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        for fp in result.get("files", []):
            assert not fp.startswith("/")  # all paths are relative


# ── Scenario: get_context before indexing ───────────────────────────────────

class TestGraphNotFound:
    def test_returns_graph_not_found_error(self, empty_repo):
        result = _call_get_context(empty_repo, "fix the payment retry logic")
        assert result.get("error") == "GRAPH_NOT_FOUND"

    def test_error_message_contains_fix_instruction(self, empty_repo):
        result = _call_get_context(empty_repo, "fix the payment retry logic")
        assert "index" in result.get("message", "").lower()

    def test_error_has_empty_files(self, empty_repo):
        result = _call_get_context(empty_repo, "fix the payment retry logic")
        assert result.get("files", []) == []

    def test_server_continues_after_graph_not_found(self, empty_repo, indexed_repo):
        # Call on empty repo first
        result1 = _call_get_context(empty_repo, "fix the payment retry logic")
        assert result1.get("error") == "GRAPH_NOT_FOUND"
        # Then call on indexed repo — should work
        result2 = _call_get_context(indexed_repo, "fix the payment retry logic")
        assert "files" in result2


# ── Scenario: Vague task string ──────────────────────────────────────────────

class TestVagueTask:
    def test_vague_task_returns_empty_bundle(self, indexed_repo):
        result = _call_get_context(indexed_repo, "make it work better")
        # Either empty files or a message — no exception
        assert "files" in result or "message" in result

    def test_vague_task_no_exception(self, indexed_repo):
        # Should not raise — returns structured response
        result = _call_get_context(indexed_repo, "")
        assert isinstance(result, dict)

    def test_vague_task_has_message(self, indexed_repo):
        result = _call_get_context(indexed_repo, "make it work better")
        if not result.get("files"):
            assert result.get("message") is not None


# ── Scenario: Refresh ────────────────────────────────────────────────────────

class TestRefresh:
    def test_refresh_returns_result_structure(self, indexed_repo):
        result = _call_refresh(indexed_repo, ["payments/processor.py"])
        assert "files_processed" in result
        assert "nodes_added" in result
        assert "nodes_removed" in result
        assert "elapsed_seconds" in result

    def test_refresh_processes_files(self, indexed_repo):
        result = _call_refresh(indexed_repo, ["payments/processor.py"])
        assert result.get("files_processed", 0) > 0

    def test_refresh_elapsed_is_positive(self, indexed_repo):
        result = _call_refresh(indexed_repo, ["payments/processor.py"])
        assert result.get("elapsed_seconds", 0) > 0


# ── Scenario: Reproducibility ────────────────────────────────────────────────

class TestReproducibility:
    def test_same_task_identical_output_five_times(self, indexed_repo):
        results = [
            _call_get_context(indexed_repo, "fix the payment retry logic")
            for _ in range(5)
        ]
        files_0 = results[0].get("files", [])
        for r in results[1:]:
            assert r.get("files", []) == files_0

    def test_token_estimate_stable_across_calls(self, indexed_repo):
        results = [
            _call_get_context(indexed_repo, "fix the payment retry logic")
            for _ in range(3)
        ]
        estimates = [r.get("token_estimate") for r in results]
        assert len(set(estimates)) == 1  # all identical


# ── Scenario: Budget override ────────────────────────────────────────────────

class TestBudgetOverride:
    def test_large_budget_returns_more_files(self, indexed_repo):
        small = _call_get_context(indexed_repo, "fix the payment retry logic", budget=100)
        large = _call_get_context(indexed_repo, "fix the payment retry logic", budget=100_000)
        assert len(large.get("files", [])) >= len(small.get("files", []))

    def test_bundle_never_exceeds_budget(self, indexed_repo):
        for budget in [500, 2000, 8000]:
            result = _call_get_context(indexed_repo, "fix the payment retry logic", budget=budget)
            assert result.get("token_estimate", 0) <= budget
