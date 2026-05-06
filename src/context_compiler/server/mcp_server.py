"""MCP server — stdio transport, exposes get_context and refresh tools.

Wires the full pipeline:
  classify → find_entry_nodes → traverse → score_and_compile → rationale
"""

from __future__ import annotations

import os
from pathlib import Path

import kuzu
from fastmcp import FastMCP

from context_compiler.indexer.graph import open_database, graph_path
from context_compiler.indexer.indexer import index_repository, refresh_repository
from context_compiler.models import ContextBundle, ErrorBundle, RefreshResult, TaskType
from context_compiler.retrieval.classifier import classify
from context_compiler.retrieval.entry_nodes import find_entry_nodes
from context_compiler.retrieval.rationale import build_excluded_list, build_rationale_list
from context_compiler.retrieval.scorer import score_and_compile
from context_compiler.retrieval.traversal import traverse

mcp = FastMCP("context-compiler")

_GRAPH_NOT_FOUND = ErrorBundle(
    error="GRAPH_NOT_FOUND",
    message="No index found at .claude-context/graph.db. Run: uvx context-compiler index --repo <path>",
)

_NO_MATCH_MESSAGE = (
    "No symbol matching the task string found in the graph. "
    "Use a more specific term (e.g., a function name, file name, or module name)."
)


def _get_repo_root() -> Path:
    """Resolve repo root from CC_REPO_PATH env var."""
    repo = os.environ.get("CC_REPO_PATH", ".")
    return Path(repo).resolve()


def _open_conn(repo_root: Path) -> kuzu.Connection | None:
    """Open a KuzuDB connection. Returns None if the graph does not exist."""
    gp = graph_path(repo_root)
    if not gp.exists():
        return None
    try:
        db = open_database(repo_root)
        return kuzu.Connection(db)
    except Exception:
        return None


@mcp.tool()
def get_context(task: str, budget: int = 0) -> dict:
    """
    Return the smallest correct context bundle for a coding task.

    Args:
        task:   Natural language description of the coding task.
        budget: Maximum token budget for the bundle (default: CC_TOKEN_BUDGET env var or 8000).

    Returns:
        ContextBundle with files, rationale, token_estimate, tokens_saved, and excluded list.
        Returns an ErrorBundle if the graph has not been indexed.
    """
    if budget <= 0:
        budget = int(os.environ.get("CC_TOKEN_BUDGET", "8000"))

    repo_root = _get_repo_root()
    conn = _open_conn(repo_root)

    if conn is None:
        return _GRAPH_NOT_FOUND.model_dump()

    try:
        # 1. Classify
        classification = classify(task)

        # 2. Find entry nodes
        match_result = find_entry_nodes(task, conn, top_k=5)

        if not match_result.candidates:
            return ContextBundle(
                task_type=classification.task_type,
                confidence=classification.confidence,
                low_confidence=True,
                token_estimate=0,
                tokens_saved=0,
                files=[],
                rationale=[],
                excluded=[],
                message=_NO_MATCH_MESSAGE,
            ).model_dump()

        # 3. Traverse
        traversal = traverse(match_result.candidates, classification.task_type, conn)

        # 4. Score and compile
        bundle = score_and_compile(traversal.candidates, budget, conn)

        # 5. Rationale
        matched_term = match_result.keywords[0] if match_result.keywords else ""
        rationale_list = build_rationale_list(bundle.included, matched_term)
        excluded_list = build_excluded_list(traversal.excluded, bundle.excluded_budget)

        # Deduplicate files (multiple symbol nodes per file → show file once)
        seen_files: dict[str, str] = {}  # file_path → rationale
        for scored_node, rationale in zip(bundle.included, rationale_list):
            fp = scored_node.candidate.file_path
            if fp not in seen_files:
                seen_files[fp] = rationale

        files = list(seen_files.keys())
        rationales = list(seen_files.values())

        # Total token estimate at file level
        file_token_map: dict[str, int] = {}
        for s in bundle.included:
            fp = s.candidate.file_path
            if fp not in file_token_map:
                file_token_map[fp] = s.candidate.token_count

        file_tokens = sum(file_token_map.values())
        total_candidate_tokens = sum(
            s.candidate.token_count for s in bundle.included + bundle.excluded_budget
        )
        tokens_saved = max(0, total_candidate_tokens - file_tokens)

        result = ContextBundle(
            task_type=classification.task_type,
            confidence=classification.confidence,
            low_confidence=classification.low_confidence,
            token_estimate=file_tokens,
            tokens_saved=tokens_saved,
            files=files,
            rationale=rationales,
            excluded=excluded_list,
            message=bundle.warning,
        )
        return result.model_dump()

    except Exception as e:
        return ErrorBundle(
            error="INTERNAL_ERROR",
            message=f"An error occurred: {e!s}",
        ).model_dump()


@mcp.tool()
def refresh(changed_files: list[str]) -> dict:
    """
    Re-index the repository to reflect file changes.

    Args:
        changed_files: List of file paths that have changed (relative to repo root).
                       In v0.1 a full re-index is performed regardless.

    Returns:
        RefreshResult with files_processed, nodes_added, nodes_removed, elapsed_seconds.
    """
    repo_root = _get_repo_root()

    try:
        result = refresh_repository(repo_root, changed_files)
        return RefreshResult(**result).model_dump()
    except Exception as e:
        return ErrorBundle(
            error="REFRESH_ERROR",
            message=f"Refresh failed: {e!s}",
        ).model_dump()


def run_server(repo_path: str) -> None:
    """Start the MCP server with stdio transport."""
    os.environ["CC_REPO_PATH"] = str(Path(repo_path).resolve())
    mcp.run()
