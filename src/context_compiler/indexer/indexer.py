"""Repository indexer — orchestrates parsing and graph construction."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import kuzu

from .graph import (
    open_database,
    create_schema,
    bulk_insert_nodes,
    bulk_insert_edges,
    node_count,
    edge_count,
    graph_path,
)
from .parser import parse_file, ParseResult

SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx"}
GITIGNORE_ENTRY = ".claude-context/"

_PLACEHOLDER_NODE = {
    "symbol_name": "",
    "symbol_type": "FILE",
    "line_start": 0,
    "line_end": 0,
    "token_count": 0,
    "last_modified": 0,
    "language": "PYTHON",
    "docstring": "",
}


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
    return [f for f in files if GITIGNORE_ENTRY.rstrip("/") not in f.parts]


def _resolve_unresolved_edges(conn: kuzu.Connection) -> None:
    """
    Resolve __unresolved__::symbol_name CALLS edges using a single batch lookup.
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

    if not unresolved:
        return

    # Batch-lookup all symbol names in one query
    symbol_names = list({uid.split("::", 1)[-1] for _, uid, _ in unresolved})
    matches_result = conn.execute(
        "MATCH (n:Node) WHERE n.symbol_name IN $names "
        "AND NOT n.id STARTS WITH '__' RETURN n.id, n.symbol_name",
        {"names": symbol_names},
    )
    symbol_to_nodes: defaultdict[str, list[str]] = defaultdict(list)
    while matches_result.has_next():
        row = matches_result.get_next()
        symbol_to_nodes[row[1]].append(row[0])

    resolved: set[tuple[str, str, str]] = set()
    for source_id, unresolved_id, edge_type in unresolved:
        sym = unresolved_id.split("::", 1)[-1]
        for target_id in symbol_to_nodes.get(sym, []):
            resolved.add((source_id, target_id, edge_type))

    bulk_insert_edges(conn, list(resolved))
    conn.execute("MATCH (n:Node) WHERE n.id STARTS WITH '__unresolved__' DETACH DELETE n")


def _resolve_covers_edges(conn: kuzu.Connection) -> None:
    """
    Resolve __covers__::module COVERS edges using Python-side path matching.
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

    if not covers:
        return

    # Fetch all FILE nodes once instead of one query per covers node
    file_result = conn.execute(
        "MATCH (n:Node) WHERE n.symbol_type = 'FILE' RETURN n.id, n.file_path"
    )
    file_nodes: list[tuple[str, str]] = []
    while file_result.has_next():
        row = file_result.get_next()
        file_nodes.append((row[0], row[1]))

    resolved: set[tuple[str, str, str]] = set()
    for source_id, covers_id in covers:
        module_ref = covers_id.split("::", 1)[-1]
        clean_ref = module_ref.lstrip("./").replace(".", "/")
        for node_id, file_path in file_nodes:
            if clean_ref in file_path:
                resolved.add((source_id, node_id, "COVERS"))

    bulk_insert_edges(conn, list(resolved))
    conn.execute("MATCH (n:Node) WHERE n.id STARTS WITH '__covers__' DETACH DELETE n")


def index_repository(repo_root: Path, verbose: bool = True) -> dict:
    """
    Full index of a repository. Overwrites any existing graph.
    Returns summary: files, nodes, edges, warnings, elapsed_seconds.
    """
    start = time.monotonic()
    repo_root = repo_root.resolve()

    _ensure_gitignore(repo_root)
    files = _collect_files(repo_root)
    warnings_list: list[str] = []

    # --- Parse all files in parallel ---
    workers = min(8, (os.cpu_count() or 4))
    parse_results: list[ParseResult] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_path = {ex.submit(parse_file, p, repo_root): p for p in files}
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            try:
                result = future.result()
            except Exception as exc:
                msg = f"WARN: skipped {path.relative_to(repo_root)} — {exc}"
                warnings_list.append(msg)
                if verbose:
                    print(msg)
                continue
            if result.error:
                msg = f"WARN: skipped {path.relative_to(repo_root)} — {result.error}"
                warnings_list.append(msg)
                if verbose:
                    print(msg)
            else:
                parse_results.append(result)

    files_indexed = len(parse_results)

    # --- Collect all nodes and edges ---
    all_node_dicts: list[dict] = []
    all_edges: list[tuple[str, str, str]] = []
    real_node_ids: set[str] = set()

    for result in parse_results:
        for node in result.nodes:
            all_node_dicts.append({
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
            real_node_ids.add(node.id)
        for edge in result.edges:
            all_edges.append((edge.source_id, edge.target_id, edge.edge_type))

    # Deduplicate nodes (same ID can appear from multiple parse results)
    seen_node_ids: set[str] = set()
    unique_node_dicts: list[dict] = []
    for nd in all_node_dicts:
        if nd["id"] not in seen_node_ids:
            seen_node_ids.add(nd["id"])
            unique_node_dicts.append(nd)
    all_node_dicts = unique_node_dicts
    real_node_ids = seen_node_ids

    # Deduplicate edges and drop edges whose source doesn't exist (parser generates
    # source IDs that don't match the corresponding node ID when the symbol name spans
    # multiple lines — the old MATCH...MERGE silently skipped these).
    all_edges = list({
        (src, tgt, et)
        for src, tgt, et in all_edges
        if src in real_node_ids
    })

    # Placeholder nodes for unresolved edge targets
    placeholder_ids = {tgt for _, tgt, _ in all_edges if tgt not in real_node_ids}
    placeholder_node_dicts = [
        {**_PLACEHOLDER_NODE, "id": pid, "file_path": pid}
        for pid in placeholder_ids
    ]

    # --- Write to DB ---
    db = open_database(repo_root)
    conn = kuzu.Connection(db)
    create_schema(conn)

    bulk_insert_nodes(conn, all_node_dicts + placeholder_node_dicts)
    bulk_insert_edges(conn, all_edges)

    # --- Resolve placeholders ---
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
