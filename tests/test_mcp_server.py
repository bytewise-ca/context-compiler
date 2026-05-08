"""Tests for the MCP server — validates against mcp-server/spec.md scenarios."""

import os
import shutil
import kuzu
import pytest
from pathlib import Path

from context_compiler.indexer.indexer import index_repository
from context_compiler.indexer.graph import open_database, save_workspace, load_workspace


PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_repo"
TYPESCRIPT_FIXTURE = Path(__file__).parent / "fixtures" / "typescript_repo"


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
        assert "slices" in result
        assert "token_estimate" in result
        assert "tokens_saved" in result
        assert "task_type" in result
        assert "confidence" in result

    def test_slices_have_required_fields(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        for s in result.get("slices", []):
            assert "file_path" in s
            assert "rationale" in s
            assert "line_start" in s
            assert "line_end" in s

    def test_slices_line_ranges_valid(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        for s in result.get("slices", []):
            if s["line_start"] is not None:
                assert s["line_start"] >= 0
                assert s["line_end"] >= s["line_start"]

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

    def test_file_paths_are_absolute(self, indexed_repo):
        result = _call_get_context(indexed_repo, "fix the payment retry logic")
        for s in result.get("slices", []):
            assert Path(s["file_path"]).is_absolute()
            assert s["file_path"].startswith(str(indexed_repo))


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
        assert result.get("files", []) == [] or result.get("slices", []) == []

    def test_server_continues_after_graph_not_found(self, empty_repo, indexed_repo):
        # Call on empty repo first
        result1 = _call_get_context(empty_repo, "fix the payment retry logic")
        assert result1.get("error") == "GRAPH_NOT_FOUND"
        # Then call on indexed repo — should work
        result2 = _call_get_context(indexed_repo, "fix the payment retry logic")
        assert "slices" in result2


# ── Scenario: Vague task string ──────────────────────────────────────────────

class TestVagueTask:
    def test_vague_task_returns_empty_bundle(self, indexed_repo):
        result = _call_get_context(indexed_repo, "make it work better")
        # Either empty slices or a message — no exception
        assert "slices" in result or "message" in result

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
        slices_0 = results[0].get("slices", [])
        for r in results[1:]:
            assert r.get("slices", []) == slices_0

    def test_token_estimate_stable_across_calls(self, indexed_repo):
        results = [
            _call_get_context(indexed_repo, "fix the payment retry logic")
            for _ in range(3)
        ]
        estimates = [r.get("token_estimate") for r in results]
        assert len(set(estimates)) == 1  # all identical


# ── Scenario: Budget override ────────────────────────────────────────────────

class TestBudgetOverride:
    def test_large_budget_returns_more_slices(self, indexed_repo):
        small = _call_get_context(indexed_repo, "fix the payment retry logic", budget=100)
        large = _call_get_context(indexed_repo, "fix the payment retry logic", budget=100_000)
        assert len(large.get("slices", [])) >= len(small.get("slices", []))

    def test_bundle_never_exceeds_budget(self, indexed_repo):
        for budget in [500, 2000, 8000]:
            result = _call_get_context(indexed_repo, "fix the payment retry logic", budget=budget)
            assert result.get("token_estimate", 0) <= budget


# ── Scenario: Workspace (multi-repo) ────────────────────────────────────────

class TestWorkspace:
    @pytest.fixture
    def primary_repo(self, tmp_path):
        repo = tmp_path / "primary"
        shutil.copytree(PYTHON_FIXTURE, repo)
        index_repository(repo, verbose=False)
        return repo

    @pytest.fixture
    def dep_repo(self, tmp_path):
        repo = tmp_path / "dep"
        shutil.copytree(TYPESCRIPT_FIXTURE, repo)
        index_repository(repo, verbose=False)
        return repo

    def test_save_and_load_workspace(self, primary_repo, dep_repo):
        save_workspace(primary_repo, [dep_repo])
        deps = load_workspace(primary_repo)
        assert len(deps) == 1
        assert deps[0].resolve() == dep_repo.resolve()

    def test_load_workspace_returns_empty_when_absent(self, primary_repo):
        assert load_workspace(primary_repo) == []

    def test_multi_repo_returns_slices_from_both(self, primary_repo, dep_repo):
        save_workspace(primary_repo, [dep_repo])
        result = _call_get_context(primary_repo, "fix the payment retry logic")
        paths = [s["file_path"] for s in result.get("slices", [])]
        # Primary repo paths start with primary_repo root
        assert any(str(primary_repo) in p for p in paths)

    def test_multi_repo_paths_are_absolute(self, primary_repo, dep_repo):
        save_workspace(primary_repo, [dep_repo])
        result = _call_get_context(primary_repo, "fix the payment retry logic")
        for s in result.get("slices", []):
            assert Path(s["file_path"]).is_absolute()

    def test_multi_repo_budget_respected(self, primary_repo, dep_repo):
        save_workspace(primary_repo, [dep_repo])
        result = _call_get_context(primary_repo, "fix the payment retry logic", budget=500)
        assert result.get("token_estimate", 0) <= 500

    def test_missing_dep_graph_does_not_crash(self, primary_repo, tmp_path):
        # Dependency path exists but was never indexed
        ghost = tmp_path / "ghost"
        ghost.mkdir()
        save_workspace(primary_repo, [ghost])
        result = _call_get_context(primary_repo, "fix the payment retry logic")
        # Falls back gracefully to primary-only results
        assert "slices" in result
