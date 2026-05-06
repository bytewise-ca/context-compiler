"""KuzuDB graph schema, creation, and write operations."""

from __future__ import annotations

import kuzu
from pathlib import Path


GRAPH_DIR = ".claude-context"
GRAPH_FILE = "graph.db"


def graph_path(repo_root: Path) -> Path:
    return repo_root / GRAPH_DIR / GRAPH_FILE


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


def node_count(conn: kuzu.Connection) -> int:
    result = conn.execute("MATCH (n:Node) RETURN COUNT(n)")
    return result.get_next()[0]


def edge_count(conn: kuzu.Connection) -> int:
    result = conn.execute("MATCH (a:Node)-[e:Edge]->(b:Node) RETURN COUNT(e)")
    return result.get_next()[0]
