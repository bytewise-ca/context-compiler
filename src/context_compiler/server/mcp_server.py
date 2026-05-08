"""MCP server — stdio transport, exposes get_context and refresh tools.

Wires the full pipeline:
  classify → find_entry_nodes → traverse → score_and_compile → rationale
"""

from __future__ import annotations

import os
from pathlib import Path

import kuzu
from fastmcp import FastMCP

from context_compiler.indexer.graph import open_database, graph_path, load_workspace
from context_compiler.indexer.indexer import index_repository, refresh_repository
from context_compiler.models import ContextBundle, ErrorBundle, FileSlice, RefreshResult, TaskType
from context_compiler.retrieval.classifier import classify
from context_compiler.retrieval.entry_nodes import find_entry_nodes
from context_compiler.retrieval.rationale import build_excluded_list, build_rationale_list
from context_compiler.retrieval.scorer import (
    score_and_compile,
    ScoredNode,
    _WARNING_ALL_FIT,
    _WARNING_BUDGET_LOW,
    _WARNING_MORE_EXIST,
)
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


def _pipeline_for_repo(
    task: str,
    task_type: TaskType,
    conn: kuzu.Connection,
    repo_root: Path,
) -> tuple[list[ScoredNode], list[dict], str]:
    """Run entry matching + traversal + scoring for one graph.

    Scores all candidates with no budget limit (budget enforced after merging).
    Converts all file paths to absolute before returning.
    Returns (scored_nodes, excluded_traversal_nodes, matched_term).
    """
    match_result = find_entry_nodes(task, conn, top_k=5)
    if not match_result.candidates:
        return [], [], ""

    traversal = traverse(match_result.candidates, task_type, conn)

    # Score with no budget limit — real budget is enforced after merging all repos
    bundle = score_and_compile(traversal.candidates, budget=10_000_000, conn=conn)

    # Make all file paths absolute
    for sn in bundle.included:
        if not Path(sn.candidate.file_path).is_absolute():
            sn.candidate.file_path = str(repo_root / sn.candidate.file_path)
    for ex in traversal.excluded:
        if not Path(ex["file_path"]).is_absolute():
            ex["file_path"] = str(repo_root / ex["file_path"])

    matched_term = match_result.keywords[0] if match_result.keywords else ""
    return bundle.included, traversal.excluded, matched_term


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
        classification = classify(task)

        # Open connections for primary repo + all workspace dependencies
        repo_conns: list[tuple[Path, kuzu.Connection]] = [(repo_root, conn)]
        for dep_root in load_workspace(repo_root):
            dep_conn = _open_conn(dep_root)
            if dep_conn is not None:
                repo_conns.append((dep_root, dep_conn))

        # Run pipeline for each repo — paths become absolute, scores computed per-graph
        all_scored: list[ScoredNode] = []
        all_excluded_traversal: list[dict] = []
        matched_term = ""
        for rr, rc in repo_conns:
            scored, excluded_t, term = _pipeline_for_repo(
                task, classification.task_type, rc, rr
            )
            all_scored.extend(scored)
            all_excluded_traversal.extend(excluded_t)
            if term and not matched_term:
                matched_term = term

        if not all_scored:
            return ContextBundle(
                task_type=classification.task_type,
                confidence=classification.confidence,
                low_confidence=True,
                token_estimate=0,
                tokens_saved=0,
                slices=[],
                excluded=[],
                message=_NO_MATCH_MESSAGE,
            ).model_dump()

        # Merge and enforce budget — same logic as score_and_compile
        all_scored.sort(key=lambda s: (-s.score, s.candidate.file_path))
        entries = [s for s in all_scored if s.candidate.is_entry]
        max_entry_tokens = max((s.candidate.token_count for s in entries), default=0)

        if max_entry_tokens > budget and entries:
            fit_entries = [s for s in entries if s.candidate.token_count <= budget] or [entries[0]]
            included = fit_entries
            fit_ids = {id(s) for s in fit_entries}
            excluded_budget = [s for s in all_scored if id(s) not in fit_ids]
            token_total = sum(s.candidate.token_count for s in included)
            warning: str | None = _WARNING_BUDGET_LOW
        else:
            included = []
            excluded_budget = []
            token_total = 0
            for sn in all_scored:
                if token_total + sn.candidate.token_count <= budget:
                    included.append(sn)
                    token_total += sn.candidate.token_count
                else:
                    excluded_budget.append(sn)
            warning = _WARNING_ALL_FIT if not excluded_budget else _WARNING_MORE_EXIST.format(n=len(excluded_budget))

        rationale_list = build_rationale_list(included, matched_term)
        excluded_list = build_excluded_list(all_excluded_traversal, excluded_budget)

        slices: list[FileSlice] = []
        for scored_node, rationale in zip(included, rationale_list):
            c = scored_node.candidate
            is_file_node = c.symbol_type == "FILE"
            slices.append(FileSlice(
                file_path=c.file_path,
                line_start=None if is_file_node else c.line_start,
                line_end=None if is_file_node else c.line_end,
                rationale=rationale,
            ))

        total_candidate_tokens = sum(s.candidate.token_count for s in all_scored)
        tokens_saved = max(0, total_candidate_tokens - token_total)

        return ContextBundle(
            task_type=classification.task_type,
            confidence=classification.confidence,
            low_confidence=classification.low_confidence,
            token_estimate=token_total,
            tokens_saved=tokens_saved,
            slices=slices,
            excluded=excluded_list,
            message=warning if excluded_budget else None,
        ).model_dump()

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
