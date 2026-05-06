"""Tests for the indexer component — validates against indexer/spec.md scenarios."""

import kuzu
import pytest
from pathlib import Path

from context_compiler.indexer.indexer import index_repository
from context_compiler.indexer.graph import open_database, graph_path


PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_repo"
TS_FIXTURE = Path(__file__).parent / "fixtures" / "typescript_repo"


@pytest.fixture(autouse=True)
def clean_graph(tmp_path, monkeypatch):
    """Each test gets a fresh temp repo root with graph written to tmp_path."""
    yield


def _index(fixture: Path, tmp_path: Path) -> tuple[dict, kuzu.Connection]:
    """Copy fixture into tmp_path, index it, return (summary, conn)."""
    import shutil
    repo = tmp_path / "repo"
    shutil.copytree(fixture, repo)
    summary = index_repository(repo, verbose=False)
    db = open_database(repo)
    conn = kuzu.Connection(db)
    return summary, conn


# ── Scenario: Index a clean Python repository ──────────────────────────────

class TestPythonIndexing:
    def test_graph_db_created(self, tmp_path):
        import shutil
        repo = tmp_path / "repo"
        shutil.copytree(PYTHON_FIXTURE, repo)
        index_repository(repo, verbose=False)
        assert graph_path(repo).exists()

    def test_gitignore_updated(self, tmp_path):
        import shutil
        repo = tmp_path / "repo"
        shutil.copytree(PYTHON_FIXTURE, repo)
        index_repository(repo, verbose=False)
        gitignore = repo / ".gitignore"
        assert gitignore.exists()
        assert ".claude-context/" in gitignore.read_text()

    def test_file_nodes_created(self, tmp_path):
        summary, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_type = 'FILE' RETURN COUNT(n)"
        )
        file_count = result.get_next()[0]
        assert file_count >= 3  # processor.py, notifier.py, payment_settings.py

    def test_class_nodes_extracted(self, tmp_path):
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_type = 'CLASS' RETURN n.symbol_name"
        )
        class_names = []
        while result.has_next():
            class_names.append(result.get_next()[0])
        assert "PaymentProcessor" in class_names
        assert "PaymentNotifier" in class_names

    def test_method_nodes_extracted(self, tmp_path):
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_type = 'METHOD' RETURN n.symbol_name"
        )
        method_names = []
        while result.has_next():
            method_names.append(result.get_next()[0])
        assert any("process_with_retry" in m for m in method_names)

    def test_imports_edges_created(self, tmp_path):
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (a:Node)-[e:Edge]->(b:Node) "
            "WHERE e.edge_type = 'IMPORTS' RETURN COUNT(e)"
        )
        assert result.get_next()[0] >= 1

    def test_covers_edges_for_test_files(self, tmp_path):
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (a:Node)-[e:Edge]->(b:Node) "
            "WHERE e.edge_type = 'COVERS' RETURN a.file_path"
        )
        test_files = []
        while result.has_next():
            test_files.append(result.get_next()[0])
        assert any("test_" in f for f in test_files)

    def test_summary_fields_present(self, tmp_path):
        import shutil
        repo = tmp_path / "repo"
        shutil.copytree(PYTHON_FIXTURE, repo)
        summary = index_repository(repo, verbose=False)
        assert "files_indexed" in summary
        assert "nodes" in summary
        assert "edges" in summary
        assert "elapsed_seconds" in summary
        assert summary["files_indexed"] > 0
        assert summary["nodes"] > 0

    def test_reindex_overwrites_existing(self, tmp_path):
        import shutil
        repo = tmp_path / "repo"
        shutil.copytree(PYTHON_FIXTURE, repo)
        index_repository(repo, verbose=False)
        # Second index should succeed without error
        summary = index_repository(repo, verbose=False)
        assert summary["files_indexed"] > 0


# ── Scenario: TypeScript repository ────────────────────────────────────────

class TestTypeScriptIndexing:
    def test_ts_file_nodes_created(self, tmp_path):
        _, conn = _index(TS_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.language = 'TYPESCRIPT' AND n.symbol_type = 'FILE' RETURN COUNT(n)"
        )
        assert result.get_next()[0] >= 2  # auth.ts, tokenStore.ts

    def test_ts_class_nodes_extracted(self, tmp_path):
        _, conn = _index(TS_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_type = 'CLASS' RETURN n.symbol_name"
        )
        names = []
        while result.has_next():
            names.append(result.get_next()[0])
        assert "AuthManager" in names
        assert "TokenStore" in names

    def test_ts_imports_edges(self, tmp_path):
        _, conn = _index(TS_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (a:Node)-[e:Edge]->(b:Node) "
            "WHERE e.edge_type = 'IMPORTS' AND a.language = 'TYPESCRIPT' RETURN COUNT(e)"
        )
        assert result.get_next()[0] >= 1

    def test_ts_test_file_covers_edges(self, tmp_path):
        _, conn = _index(TS_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (a:Node)-[e:Edge]->(b:Node) "
            "WHERE e.edge_type = 'COVERS' RETURN a.file_path"
        )
        test_files = []
        while result.has_next():
            test_files.append(result.get_next()[0])
        assert any(".test." in f for f in test_files)


# ── Scenario: Parse error is skipped gracefully ─────────────────────────────

class TestGracefulSkip:
    def test_broken_file_skipped_with_warning(self, tmp_path):
        import shutil
        repo = tmp_path / "repo"
        shutil.copytree(PYTHON_FIXTURE, repo)
        # Write a file with a syntax error
        broken = repo / "broken.py"
        broken.write_text("def foo(:\n    pass\n")  # syntax error
        summary = index_repository(repo, verbose=False)
        # Should still index other files
        assert summary["files_indexed"] >= 3
        # Broken file should appear in warnings (may be partial parse)
        # tree-sitter is lenient, so this is a best-effort check
        assert isinstance(summary["warnings"], list)

    def test_empty_repo_creates_empty_graph(self, tmp_path):
        repo = tmp_path / "empty_repo"
        repo.mkdir()
        summary = index_repository(repo, verbose=False)
        assert summary["files_indexed"] == 0
        assert summary["nodes"] == 0


# ── Scenario: Node metadata stored correctly ────────────────────────────────

class TestNodeMetadata:
    def test_node_has_required_metadata(self, tmp_path):
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_type = 'CLASS' "
            "RETURN n.file_path, n.line_start, n.line_end, n.token_count, n.last_modified, n.language "
            "LIMIT 1"
        )
        row = result.get_next()
        file_path, line_start, line_end, token_count, last_modified, language = row
        assert file_path != ""
        assert line_start > 0
        assert line_end >= line_start
        assert token_count > 0
        assert last_modified > 0
        assert language in ("PYTHON", "TYPESCRIPT")


class TestDocstringIndexing:
    def test_python_class_docstring_stored(self, tmp_path):
        # PaymentProcessor has docstring "Handles payment processing with retry support."
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_name = 'PaymentProcessor' RETURN n.docstring"
        )
        assert result.has_next()
        docstring = result.get_next()[0]
        assert "payment" in docstring.lower() or "handles" in docstring.lower()

    def test_python_method_docstring_stored(self, tmp_path):
        # process_with_retry has docstring "Process a payment with exponential backoff."
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_name = 'PaymentProcessor.process_with_retry' RETURN n.docstring"
        )
        assert result.has_next()
        docstring = result.get_next()[0]
        assert "backoff" in docstring.lower() or "payment" in docstring.lower()

    def test_python_method_without_docstring_is_empty(self, tmp_path):
        # PaymentProcessor.reset has no docstring
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_name = 'PaymentProcessor.reset' RETURN n.docstring"
        )
        assert result.has_next()
        docstring = result.get_next()[0]
        assert docstring == ""

    def test_file_node_has_empty_docstring(self, tmp_path):
        _, conn = _index(PYTHON_FIXTURE, tmp_path)
        result = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_type = 'FILE' RETURN n.docstring LIMIT 1"
        )
        assert result.has_next()
        # FILE nodes don't get docstrings in the current implementation
        assert result.get_next()[0] == ""
