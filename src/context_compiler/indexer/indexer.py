"""Repository indexer — orchestrates parsing and graph construction."""

from __future__ import annotations

import time
import kuzu
from pathlib import Path

from .graph import (
    open_database,
    create_schema,
    clear_file_nodes,
    insert_node,
    insert_edge,
    node_count,
    edge_count,
    graph_path,
)
from .parser import parse_file, ParseResult

SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx"}
GITIGNORE_ENTRY = ".claude-context/"


def _ensure_gitignore(repo_root: Path) -> None:
    gitignore = repo_root / ".gitignore"
    entry = GITIGNORE_ENTRY
    if gitignore.exists():
        content = gitignore.read_text()
        if entry not in content:
            gitignore.write_text(content.rstrip() + f"\n{entry}\n")
    else:
        gitignore.write_text(f"{entry}\n")


def _collect_files(repo_root: Path) -> list[Path]:
    files = []
    for ext in SUPPORTED_EXTENSIONS:
        files.extend(repo_root.rglob(f"*{ext}"))
    # Exclude files inside .claude-context itself
    return [f for f in files if GITIGNORE_ENTRY.rstrip("/") not in f.parts]


def _resolve_unresolved_edges(conn: kuzu.Connection) -> None:
    """
    Resolve __unresolved__::symbol_name CALLS edges to real node IDs.
    For each unresolved target, find nodes whose symbol_name matches.
    """
    result = conn.execute(
        "MATCH (a:Node)-[e:Edge]->(b:Node) "
        "WHERE b.id STARTS WITH '__unresolved__' "
        "RETURN a.id, b.id, e.edge_type"
    )
    unresolved = []
    while result.has_next():
        row = result.get_next()
        unresolved.append((row[0], row[1], row[2]))

    for source_id, unresolved_id, edge_type in unresolved:
        symbol_name = unresolved_id.split("::", 1)[-1]
        # Find all nodes with matching symbol_name
        matches = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_name = $name RETURN n.id",
            {"name": symbol_name},
        )
        while matches.has_next():
            target_id = matches.get_next()[0]
            conn.execute(
                "MATCH (a:Node {id: $src}), (b:Node {id: $tgt}) "
                "MERGE (a)-[e:Edge {edge_type: $et}]->(b)",
                {"src": source_id, "tgt": target_id, "et": edge_type},
            )
        # Remove the unresolved placeholder node and its edges
        conn.execute(
            "MATCH (n:Node {id: $id}) DETACH DELETE n",
            {"id": unresolved_id},
        )


def _resolve_covers_edges(conn: kuzu.Connection) -> None:
    """
    Resolve __covers__::module COVERS edges to real file node IDs.
    """
    result = conn.execute(
        "MATCH (a:Node)-[e:Edge]->(b:Node) "
        "WHERE b.id STARTS WITH '__covers__' "
        "RETURN a.id, b.id"
    )
    covers = []
    while result.has_next():
        row = result.get_next()
        covers.append((row[0], row[1]))

    for source_id, covers_id in covers:
        module_ref = covers_id.split("::", 1)[-1]
        # Strip leading ./ or ../ (TypeScript relative imports) and dots (Python module paths)
        clean_ref = module_ref.lstrip("./").replace(".", "/")
        module_path_fragment = clean_ref
        matches = conn.execute(
            "MATCH (n:Node) WHERE n.symbol_type = 'FILE' "
            "AND n.file_path CONTAINS $fragment RETURN n.id",
            {"fragment": module_path_fragment},
        )
        while matches.has_next():
            target_id = matches.get_next()[0]
            conn.execute(
                "MATCH (a:Node {id: $src}), (b:Node {id: $tgt}) "
                "MERGE (a)-[e:Edge {edge_type: 'COVERS'}]->(b)",
                {"src": source_id, "tgt": target_id},
            )
        conn.execute(
            "MATCH (n:Node {id: $id}) DETACH DELETE n",
            {"id": covers_id},
        )


def index_repository(repo_root: Path, verbose: bool = True) -> dict:
    """
    Full index of a repository. Overwrites any existing graph.
    Returns summary: files, nodes, edges, warnings, elapsed_seconds.
    """
    start = time.monotonic()
    repo_root = repo_root.resolve()

    _ensure_gitignore(repo_root)

    files = _collect_files(repo_root)
    warnings_list = []
    files_indexed = 0

    db = open_database(repo_root)
    conn = kuzu.Connection(db)
    create_schema(conn)

    # Full reindex: drop all existing data
    conn.execute("MATCH (n:Node) DETACH DELETE n")

    for path in files:
        result: ParseResult = parse_file(path, repo_root)
        if result.error:
            warnings_list.append(f"WARN: skipped {path.relative_to(repo_root)} — {result.error}")
            if verbose:
                print(warnings_list[-1])
            continue

        for node in result.nodes:
            insert_node(conn, {
                "id": node.id,
                "file_path": node.file_path,
                "symbol_name": node.symbol_name or "",
                "symbol_type": node.symbol_type,
                "line_start": node.line_start,
                "line_end": node.line_end,
                "token_count": node.token_count,
                "last_modified": node.last_modified,
                "language": node.language,
                "docstring": node.docstring,
            })

        for edge in result.edges:
            # Insert placeholder nodes for unresolved targets so edges can be inserted
            _ensure_placeholder(conn, edge.target_id)
            insert_edge(conn, edge.source_id, edge.target_id, edge.edge_type)

        files_indexed += 1

    # Post-processing: resolve unresolved edges
    _resolve_unresolved_edges(conn)
    _resolve_covers_edges(conn)

    n_nodes = node_count(conn)
    n_edges = edge_count(conn)
    elapsed = time.monotonic() - start

    summary = {
        "files_indexed": files_indexed,
        "files_skipped": len(warnings_list),
        "nodes": n_nodes,
        "edges": n_edges,
        "elapsed_seconds": round(elapsed, 2),
        "warnings": warnings_list,
    }

    if verbose:
        print(
            f"\nIndexing complete: {files_indexed} files, "
            f"{n_nodes} nodes, {n_edges} edges "
            f"in {elapsed:.1f}s"
        )

    return summary


def _ensure_placeholder(conn: kuzu.Connection, node_id: str) -> None:
    """Insert a minimal placeholder node if it doesn't exist. Used for unresolved refs."""
    conn.execute(
        """
        MERGE (n:Node {id: $id})
        ON CREATE SET
            n.file_path = $id,
            n.symbol_name = '',
            n.symbol_type = 'FILE',
            n.line_start = 0,
            n.line_end = 0,
            n.token_count = 0,
            n.last_modified = 0,
            n.language = 'PYTHON',
            n.docstring = ''
        """,
        {"id": node_id},
    )


def refresh_repository(repo_root: Path, changed_files: list[str]) -> dict:
    """
    Re-index the full repository (v0.1: full reindex regardless of changed_files).
    Returns RefreshResult-compatible dict.
    """
    repo_root = repo_root.resolve()
    db = open_database(repo_root)
    conn = kuzu.Connection(db)

    nodes_before = node_count(conn)
    summary = index_repository(repo_root, verbose=False)
    nodes_after = node_count(conn)

    return {
        "files_processed": summary["files_indexed"],
        "nodes_added": max(0, nodes_after - nodes_before),
        "nodes_removed": max(0, nodes_before - nodes_after),
        "elapsed_seconds": summary["elapsed_seconds"],
    }
