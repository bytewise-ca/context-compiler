"""KuzuDB graph schema, creation, and write operations."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import kuzu
from pathlib import Path


GRAPH_DIR = ".claude-context"
GRAPH_FILE = "graph.db"
WORKSPACE_FILE = "workspace.json"


def _clean(s: str) -> str:
    """Sanitize a string field for CSV writing.

    KuzuDB's COPY FROM does not correctly parse RFC 4180 quoted fields — commas
    inside a quoted field are treated as column separators. Replace commas, quotes,
    and newlines so no field ever needs quoting.
    """
    return (
        s.replace('\r\n', ' ')
         .replace('\n', ' ')
         .replace('\r', ' ')
         .replace('"', "'")
         .replace(',', ' ')
    )


def graph_path(repo_root: Path) -> Path:
    return repo_root / GRAPH_DIR / GRAPH_FILE


def save_workspace(repo_root: Path, dependencies: list[Path]) -> None:
    """Persist dependency repo paths alongside the primary graph."""
    config = {"dependencies": [str(d.resolve()) for d in dependencies]}
    config_path = repo_root / GRAPH_DIR / WORKSPACE_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2))


def load_workspace(repo_root: Path) -> list[Path]:
    """Return dependency repo paths saved by a previous init --dependencies call."""
    config_path = repo_root / GRAPH_DIR / WORKSPACE_FILE
    if not config_path.exists():
        return []
    try:
        config = json.loads(config_path.read_text())
        return [Path(p) for p in config.get("dependencies", [])]
    except Exception:
        return []


def drop_database(repo_root: Path) -> None:
    """Delete the graph DB so the next open starts completely fresh."""
    import shutil
    db_path = graph_path(repo_root)
    if db_path.is_dir():
        shutil.rmtree(db_path)
    elif db_path.exists():
        db_path.unlink()


def open_database(repo_root: Path) -> kuzu.Database:
    db_path = graph_path(repo_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return kuzu.Database(str(db_path))


def create_schema(conn: kuzu.Connection) -> None:
    """Create node and edge tables. Drops and recreates to handle schema migrations."""
    conn.execute("DROP TABLE IF EXISTS Edge")
    conn.execute("DROP TABLE IF EXISTS Node")
    conn.execute("""
        CREATE NODE TABLE Node (
            id STRING PRIMARY KEY,
            file_path STRING,
            symbol_name STRING,
            symbol_type STRING,
            line_start INT64,
            line_end INT64,
            token_count INT64,
            last_modified INT64,
            language STRING,
            docstring STRING
        )
    """)
    conn.execute("""
        CREATE REL TABLE Edge (
            FROM Node TO Node,
            edge_type STRING
        )
    """)


def clear_file_nodes(conn: kuzu.Connection, file_path: str) -> dict:
    """Remove all nodes (and their edges) for a given file path. Returns counts."""
    result = conn.execute(
        "MATCH (n:Node) WHERE n.file_path = $fp RETURN COUNT(n)",
        {"fp": file_path},
    )
    removed = result.get_next()[0]
    conn.execute("MATCH (n:Node) WHERE n.file_path = $fp DETACH DELETE n", {"fp": file_path})
    return {"nodes_removed": removed}


def insert_node(conn: kuzu.Connection, node: dict) -> None:
    conn.execute(
        """
        MERGE (n:Node {id: $id})
        SET n.file_path = $file_path,
            n.symbol_name = $symbol_name,
            n.symbol_type = $symbol_type,
            n.line_start = $line_start,
            n.line_end = $line_end,
            n.token_count = $token_count,
            n.last_modified = $last_modified,
            n.language = $language,
            n.docstring = $docstring
        """,
        node,
    )


def insert_edge(conn: kuzu.Connection, source_id: str, target_id: str, edge_type: str) -> None:
    conn.execute(
        """
        MATCH (a:Node {id: $src}), (b:Node {id: $tgt})
        MERGE (a)-[e:Edge {edge_type: $et}]->(b)
        """,
        {"src": source_id, "tgt": target_id, "et": edge_type},
    )


def bulk_insert_nodes(conn: kuzu.Connection, nodes: list[dict]) -> None:
    """Bulk insert nodes via COPY FROM CSV. ~500x faster than row-by-row MERGE."""
    if not nodes:
        return
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as f:
        tmp_path = f.name
        writer = csv.writer(f)
        writer.writerow(['id', 'file_path', 'symbol_name', 'symbol_type',
                         'line_start', 'line_end', 'token_count',
                         'last_modified', 'language', 'docstring'])
        for n in nodes:
            writer.writerow([
                _clean(n['id']), _clean(n['file_path']), _clean(n['symbol_name']),
                n['symbol_type'], n['line_start'], n['line_end'],
                n['token_count'], n['last_modified'], n['language'],
                _clean(n['docstring']),
            ])
    try:
        conn.execute(f"COPY Node FROM '{tmp_path}' (HEADER=TRUE, PARALLEL=FALSE)")
        # KuzuDB maps empty CSV fields to NULL — normalize back to empty string
        conn.execute(
            "MATCH (n:Node) WHERE n.symbol_name IS NULL SET n.symbol_name = ''"
        )
        conn.execute(
            "MATCH (n:Node) WHERE n.docstring IS NULL SET n.docstring = ''"
        )
    finally:
        os.unlink(tmp_path)


def bulk_insert_edges(conn: kuzu.Connection, edges: list[tuple[str, str, str]]) -> None:
    """Bulk insert edges via COPY FROM CSV. Edges is a list of (source_id, target_id, edge_type)."""
    if not edges:
        return
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as f:
        tmp_path = f.name
        writer = csv.writer(f)
        writer.writerow(['from', 'to', 'edge_type'])
        for src, tgt, et in edges:
            writer.writerow([_clean(src), _clean(tgt), et])
    try:
        conn.execute(f"COPY Edge FROM '{tmp_path}' (HEADER=TRUE, PARALLEL=FALSE)")
    finally:
        os.unlink(tmp_path)


def node_count(conn: kuzu.Connection) -> int:
    result = conn.execute("MATCH (n:Node) RETURN COUNT(n)")
    return result.get_next()[0]


def edge_count(conn: kuzu.Connection) -> int:
    result = conn.execute("MATCH (a:Node)-[e:Edge]->(b:Node) RETURN COUNT(e)")
    return result.get_next()[0]
