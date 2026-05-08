"""CLI entry points: init, index, explain, serve."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

_REPO_OPTION = click.option(
    "--repo",
    default=".",
    show_default=True,
    type=click.Path(exists=True),
    help="Path to repository root (defaults to current directory)",
)


_CLAUDE_MD_BLOCK = """\

## Context retrieval

Before reading any source files, call the `get_context` MCP tool with your task description.
It returns a `slices` list. For each slice:
- If `line_start` is null, read the whole file.
- Otherwise, read only the relevant symbol: `offset=line_start, limit=line_end-line_start+1`.
If `get_context` returns `GRAPH_NOT_FOUND`, run `context-compiler init`.
"""

_CLAUDE_MD_MARKER = "get_context"


def _update_claude_md(repo_root: Path) -> str:
    """Append context-retrieval instructions to CLAUDE.md if not already present."""
    claude_md = repo_root / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text()
        if _CLAUDE_MD_MARKER in content:
            return "skip"
        claude_md.write_text(content.rstrip() + "\n" + _CLAUDE_MD_BLOCK)
        return "updated"
    else:
        claude_md.write_text(_CLAUDE_MD_BLOCK.lstrip())
        return "created"


def _register_mcp(repo_root: Path) -> str:
    """Register the MCP server with Claude Code. Returns status string."""
    if not shutil.which("claude"):
        return "skip"  # claude CLI not on PATH

    binary = shutil.which("context-compiler") or "context-compiler"

    # Remove stale registration silently, then re-add
    subprocess.run(
        ["claude", "mcp", "remove", "context-compiler"],
        capture_output=True,
    )
    result = subprocess.run(
        [
            "claude", "mcp", "add", "--scope", "user",
            "context-compiler", binary,
            "--", "serve", "--repo", str(repo_root),
        ],
        capture_output=True,
        text=True,
    )
    return "ok" if result.returncode == 0 else f"failed: {result.stderr.strip()}"


@click.group()
def cli():
    """context-compiler: local context compiler for AI coding assistants."""
    pass


@cli.command()
@_REPO_OPTION
@click.option(
    "--dependencies",
    default="",
    help="Comma-separated paths to dependency repos (each indexed into its own graph)",
)
def init(repo: str, dependencies: str):
    """Index the repo, register the MCP server, and update CLAUDE.md."""
    from context_compiler.indexer.indexer import index_repository
    from context_compiler.indexer.graph import save_workspace

    repo_root = Path(repo).resolve()

    # 1. Index primary repo
    click.echo(f"Indexing {repo_root} ...")
    summary = index_repository(repo_root, verbose=False)
    click.echo(
        f"  {summary['files_indexed']} files · "
        f"{summary['nodes']} nodes · "
        f"{summary['edges']} edges · "
        f"{summary['elapsed_seconds']:.1f}s"
    )
    if summary["warnings"]:
        click.echo(f"  {len(summary['warnings'])} file(s) skipped due to parse errors.")

    # 2. Index dependencies
    dep_roots: list[Path] = []
    if dependencies:
        for raw in dependencies.split(","):
            dep_root = Path(raw.strip()).resolve()
            if not dep_root.exists():
                click.echo(f"  WARNING: dependency not found, skipping: {dep_root}")
                continue
            click.echo(f"Indexing dependency {dep_root} ...")
            dep_summary = index_repository(dep_root, verbose=False)
            click.echo(
                f"  {dep_summary['files_indexed']} files · "
                f"{dep_summary['nodes']} nodes · "
                f"{dep_summary['edges']} edges · "
                f"{dep_summary['elapsed_seconds']:.1f}s"
            )
            dep_roots.append(dep_root)
        save_workspace(repo_root, dep_roots)
        click.echo(f"  Workspace saved ({len(dep_roots)} dependencies).")

    # 3. Register MCP server
    mcp_status = _register_mcp(repo_root)
    if mcp_status == "ok":
        click.echo("  MCP server registered with Claude Code.")
    elif mcp_status == "skip":
        click.echo("  claude CLI not found — skipping MCP registration.")
        click.echo("  Run manually: claude mcp add --scope user context-compiler context-compiler -- serve --repo .")
    else:
        click.echo(f"  MCP registration {mcp_status}")

    # 3. Update CLAUDE.md
    md_status = _update_claude_md(repo_root)
    if md_status == "created":
        click.echo("  CLAUDE.md created with context-retrieval instructions.")
    elif md_status == "updated":
        click.echo("  CLAUDE.md updated with context-retrieval instructions.")
    else:
        click.echo("  CLAUDE.md already contains context-retrieval instructions.")

    click.echo("\nDone. Open Claude Code in this repo and start coding.")


@cli.command()
@_REPO_OPTION
def index(repo: str):
    """Index a repository into the dependency graph."""
    from context_compiler.indexer.indexer import index_repository

    summary = index_repository(Path(repo), verbose=True)
    if summary["warnings"]:
        click.echo(f"\n{len(summary['warnings'])} file(s) skipped due to parse errors.")
    sys.exit(0)


@cli.command()
@_REPO_OPTION
@click.option("--task", required=True, help="Task string to explain context for")
@click.option("--budget", default=8000, show_default=True, help="Token budget")
def explain(repo: str, task: str, budget: int):
    """Show the context bundle that would be returned for a task."""
    import kuzu
    from context_compiler.indexer.graph import open_database, graph_path
    from context_compiler.retrieval.classifier import classify
    from context_compiler.retrieval.entry_nodes import find_entry_nodes
    from context_compiler.retrieval.rationale import (
        build_excluded_list,
        build_rationale_list,
        render_explain_report,
    )
    from context_compiler.retrieval.scorer import score_and_compile
    from context_compiler.retrieval.traversal import traverse

    repo_root = Path(repo).resolve()
    gp = graph_path(repo_root)

    if not gp.exists():
        click.echo(
            f"ERROR: No index found. Run:\n"
            f"  context-compiler index",
            err=True,
        )
        sys.exit(1)

    db = open_database(repo_root)
    conn = kuzu.Connection(db)

    classification = classify(task)
    match_result = find_entry_nodes(task, conn, top_k=5)

    if not match_result.candidates:
        click.echo(f'No symbol matching "{task}" found in graph.')
        click.echo("Use a more specific term (e.g., a function name, file name, or module name).")
        sys.exit(0)

    traversal = traverse(match_result.candidates, classification.task_type, conn)
    bundle = score_and_compile(traversal.candidates, budget, conn)

    matched_term = match_result.keywords[0] if match_result.keywords else ""
    rationale_list = build_rationale_list(bundle.included, matched_term)
    excluded_list = build_excluded_list(traversal.excluded, bundle.excluded_budget)

    included_display: list[tuple[str, str]] = []
    for scored_node, rationale in zip(bundle.included, rationale_list):
        c = scored_node.candidate
        if c.symbol_type == "FILE":
            label = c.file_path
        else:
            label = f"{c.file_path}:{c.line_start}-{c.line_end}"
        included_display.append((label, rationale))

    token_estimate = sum(s.candidate.token_count for s in bundle.included)
    total_tokens = token_estimate + sum(s.candidate.token_count for s in bundle.excluded_budget)

    report = render_explain_report(
        task=task,
        task_type=classification.task_type.value,
        confidence=classification.confidence,
        token_estimate=token_estimate,
        tokens_saved=max(0, total_tokens - token_estimate),
        included=included_display,
        excluded=excluded_list,
    )
    click.echo(report)


@cli.command()
@_REPO_OPTION
def serve(repo: str):
    """Start the MCP server (stdio transport). Intended to be called by Claude Code."""
    from context_compiler.server.mcp_server import run_server
    run_server(repo)


if __name__ == "__main__":
    cli()
