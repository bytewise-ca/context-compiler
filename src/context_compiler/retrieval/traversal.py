"""Graph traversal — BFS per task type.

Each task type uses a different traversal strategy:
  BUG_FIX:    inbound CALLS edges + COVERS edges, depth ≤ 2
  NEW_FEATURE: IMPORTS edges + sibling symbols in same file, depth ≤ 2
  REFACTOR:   outbound CALLS edges + ALL COVERS edges, depth ≤ 3
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import kuzu

from context_compiler.models import TaskType
from context_compiler.retrieval.entry_nodes import EntryNode

_DEPTH_LIMITS: dict[TaskType, int] = {
    TaskType.BUG_FIX: 2,
    TaskType.NEW_FEATURE: 2,
    TaskType.REFACTOR: 3,
}


@dataclass
class CandidateNode:
    node_id: str
    file_path: str
    symbol_name: str | None
    symbol_type: str
    line_start: int
    line_end: int
    token_count: int
    last_modified: int
    language: str
    depth: int
    edge_type: str        # CALLS | IMPORTS | COVERS | ENTRY
    source_id: str | None  # which node led to this one
    is_entry: bool = False


@dataclass
class TraversalResult:
    candidates: list[CandidateNode]
    excluded: list[dict]   # {"node_id", "file_path", "reason"}


def traverse(
    entry_nodes: list[EntryNode],
    task_type: TaskType,
    conn: kuzu.Connection,
) -> TraversalResult:
    """
    Run BFS from all entry nodes using the strategy for the given task type.
    Merges results — nodes reachable from multiple entries get the minimum depth.
    """
    depth_limit = _DEPTH_LIMITS[task_type]
    visited: dict[str, CandidateNode] = {}   # node_id → best CandidateNode
    excluded: list[dict] = []
    depth_exceeded: set[str] = set()

    # Seed with entry nodes
    queue: deque[tuple[str, int, str, str | None]] = deque()  # (node_id, depth, edge_type, source_id)

    for entry in entry_nodes:
        meta = _fetch_node_meta(entry.node_id, conn)
        if meta is None:
            continue
        candidate = CandidateNode(
            node_id=entry.node_id,
            file_path=entry.file_path,
            symbol_name=entry.symbol_name,
            symbol_type=entry.symbol_type,
            line_start=meta["line_start"],
            line_end=meta["line_end"],
            token_count=meta["token_count"],
            last_modified=meta["last_modified"],
            language=meta["language"],
            depth=0,
            edge_type="ENTRY",
            source_id=None,
            is_entry=True,
        )
        if entry.node_id not in visited:
            visited[entry.node_id] = candidate
        queue.append((entry.node_id, 0, "ENTRY", None))

    # BFS
    while queue:
        node_id, depth, via_edge, source_id = queue.popleft()

        if depth >= depth_limit:
            continue

        neighbours = _get_neighbours(node_id, task_type, conn)

        for neighbour_id, edge_type in neighbours:
            if neighbour_id.startswith("__"):
                continue  # skip placeholder nodes

            new_depth = depth + 1

            if neighbour_id in visited:
                # Update depth if we found a shorter path
                if new_depth < visited[neighbour_id].depth:
                    visited[neighbour_id].depth = new_depth
                continue

            meta = _fetch_node_meta(neighbour_id, conn)
            if meta is None:
                continue

            candidate = CandidateNode(
                node_id=neighbour_id,
                file_path=meta["file_path"],
                symbol_name=meta["symbol_name"] or None,
                symbol_type=meta["symbol_type"],
                line_start=meta["line_start"],
                line_end=meta["line_end"],
                token_count=meta["token_count"],
                last_modified=meta["last_modified"],
                language=meta["language"],
                depth=new_depth,
                edge_type=edge_type,
                source_id=node_id,
                is_entry=False,
            )
            visited[neighbour_id] = candidate
            queue.append((neighbour_id, new_depth, edge_type, node_id))

        # Track nodes that exist but exceed depth — only for non-COVERS edges
        if task_type != TaskType.NEW_FEATURE:
            over_depth = _get_over_depth_neighbours(node_id, task_type, conn, depth_limit, depth)
            for nid, reason in over_depth:
                if nid not in visited and nid not in depth_exceeded and not nid.startswith("__"):
                    depth_exceeded.add(nid)
                    nmeta = _fetch_node_meta(nid, conn)
                    fp = nmeta["file_path"] if nmeta else nid
                    excluded.append({
                        "node_id": nid,
                        "file_path": fp,
                        "reason": reason,
                    })

    candidates = list(visited.values())
    return TraversalResult(candidates=candidates, excluded=excluded)


def _get_neighbours(
    node_id: str,
    task_type: TaskType,
    conn: kuzu.Connection,
) -> list[tuple[str, str]]:
    """Return (neighbour_id, edge_type) pairs for the given traversal strategy."""
    results = []

    if task_type == TaskType.BUG_FIX:
        # Inbound CALLS: who calls this node?
        r = conn.execute(
            "MATCH (caller:Node)-[e:Edge]->(target:Node {id: $id}) "
            "WHERE e.edge_type = 'CALLS' RETURN caller.id",
            {"id": node_id},
        )
        while r.has_next():
            results.append((r.get_next()[0], "CALLS"))

        # COVERS: test files covering this node
        r = conn.execute(
            "MATCH (test:Node)-[e:Edge]->(target:Node {id: $id}) "
            "WHERE e.edge_type = 'COVERS' RETURN test.id",
            {"id": node_id},
        )
        while r.has_next():
            results.append((r.get_next()[0], "COVERS"))

        # IMPORTS: what this node imports (depth-limited)
        r = conn.execute(
            "MATCH (src:Node {id: $id})-[e:Edge]->(imp:Node) "
            "WHERE e.edge_type = 'IMPORTS' RETURN imp.id",
            {"id": node_id},
        )
        while r.has_next():
            results.append((r.get_next()[0], "IMPORTS"))

    elif task_type == TaskType.NEW_FEATURE:
        # IMPORTS: what this node imports
        r = conn.execute(
            "MATCH (src:Node {id: $id})-[e:Edge]->(imp:Node) "
            "WHERE e.edge_type = 'IMPORTS' RETURN imp.id",
            {"id": node_id},
        )
        while r.has_next():
            results.append((r.get_next()[0], "IMPORTS"))

        # Siblings: other symbols defined in the same file
        r = conn.execute(
            "MATCH (src:Node {id: $id}), (sib:Node) "
            "WHERE sib.file_path = src.file_path AND sib.id <> src.id "
            "AND sib.symbol_type IN ['FUNCTION', 'CLASS', 'METHOD'] "
            "RETURN sib.id",
            {"id": node_id},
        )
        while r.has_next():
            results.append((r.get_next()[0], "DEFINED_IN"))

    elif task_type == TaskType.REFACTOR:
        # Outbound CALLS: who calls this node (blast radius)
        r = conn.execute(
            "MATCH (caller:Node)-[e:Edge]->(target:Node {id: $id}) "
            "WHERE e.edge_type = 'CALLS' RETURN caller.id",
            {"id": node_id},
        )
        while r.has_next():
            results.append((r.get_next()[0], "CALLS"))

        # ALL COVERS edges
        r = conn.execute(
            "MATCH (test:Node)-[e:Edge]->(target:Node {id: $id}) "
            "WHERE e.edge_type = 'COVERS' RETURN test.id",
            {"id": node_id},
        )
        while r.has_next():
            results.append((r.get_next()[0], "COVERS"))

    return results


def _get_over_depth_neighbours(
    node_id: str,
    task_type: TaskType,
    conn: kuzu.Connection,
    depth_limit: int,
    current_depth: int,
) -> list[tuple[str, str]]:
    """Return neighbours that would exceed the depth limit, for exclusion tracking."""
    if current_depth < depth_limit - 1:
        return []  # Only track at the boundary depth

    results = []
    exceeded_depth = current_depth + 2  # one beyond limit

    if task_type in (TaskType.BUG_FIX, TaskType.REFACTOR):
        r = conn.execute(
            "MATCH (caller:Node)-[e:Edge]->(target:Node {id: $id}) "
            "WHERE e.edge_type = 'CALLS' RETURN caller.id",
            {"id": node_id},
        )
        while r.has_next():
            nid = r.get_next()[0]
            results.append((
                nid,
                f"depth {exceeded_depth}, exceeds limit of {depth_limit}",
            ))

    return results


def _fetch_node_meta(node_id: str, conn: kuzu.Connection) -> dict | None:
    """Fetch node metadata from the graph. Returns None if not found."""
    r = conn.execute(
        "MATCH (n:Node {id: $id}) "
        "RETURN n.file_path, n.symbol_name, n.symbol_type, "
        "n.token_count, n.last_modified, n.language, n.line_start, n.line_end",
        {"id": node_id},
    )
    if not r.has_next():
        return None
    row = r.get_next()
    return {
        "file_path": row[0],
        "symbol_name": row[1],
        "symbol_type": row[2],
        "token_count": row[3],
        "last_modified": row[4],
        "language": row[5],
        "line_start": row[6],
        "line_end": row[7],
    }
