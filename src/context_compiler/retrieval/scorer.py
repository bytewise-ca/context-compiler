"""Node scorer and token-budget-aware bundle compiler.

Scoring formula (per spec FR-04.1):
  score = 0.40 × call_weight
        + 0.25 × recency_weight
        + 0.20 × test_coverage_weight
        + 0.15 × symbol_frequency
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import kuzu

from context_compiler.retrieval.traversal import CandidateNode

_CALL_WEIGHTS = {0: 1.0, 1: 1.0, 2: 0.6, 3: 0.3}
_CALL_WEIGHT_DEFAULT = 0.1

_WARNING_ALL_FIT = "All relevant nodes included; budget not reached."
_WARNING_BUDGET_LOW = "Budget too low to include dependencies. Increase budget for better context."
_WARNING_MORE_EXIST = "Note: {n} additional relevant nodes exist. Call get_context with a higher budget to include them."


@dataclass
class ScoredNode:
    candidate: CandidateNode
    score: float
    call_weight: float
    recency_weight: float
    test_coverage_weight: float
    symbol_frequency: float


@dataclass
class BundleResult:
    included: list[ScoredNode]
    excluded_budget: list[ScoredNode]   # excluded due to budget exhaustion
    token_estimate: int
    tokens_saved: int
    warning: str | None


def _call_weight(depth: int, edge_type: str) -> float:
    if edge_type == "ENTRY":
        return 1.0
    return _CALL_WEIGHTS.get(depth, _CALL_WEIGHT_DEFAULT)


def _recency_weight(last_modified: int, oldest: int, newest: int) -> float:
    if newest == oldest:
        return 1.0
    return (last_modified - oldest) / (newest - oldest)


def _test_coverage_weight(node_id: str, conn: kuzu.Connection) -> float:
    r = conn.execute(
        "MATCH (t:Node)-[e:Edge]->(n:Node {id: $id}) "
        "WHERE e.edge_type = 'COVERS' RETURN COUNT(t)",
        {"id": node_id},
    )
    return 1.0 if r.get_next()[0] > 0 else 0.0


def _symbol_frequencies(candidates: list[CandidateNode], conn: kuzu.Connection) -> dict[str, float]:
    """Compute normalised symbol frequency for all candidates in one pass."""
    counts: dict[str, int] = {}
    for c in candidates:
        r = conn.execute(
            "MATCH (a:Node)-[e:Edge]->(b:Node {id: $id}) RETURN COUNT(a)",
            {"id": c.node_id},
        )
        counts[c.node_id] = r.get_next()[0]

    max_count = max(counts.values(), default=1) or 1
    return {nid: count / max_count for nid, count in counts.items()}


def score_and_compile(
    candidates: list[CandidateNode],
    budget: int,
    conn: kuzu.Connection,
) -> BundleResult:
    """
    Score all candidates and greedily include them within the token budget.
    Entry nodes are always included first.
    """
    if not candidates:
        return BundleResult(
            included=[],
            excluded_budget=[],
            token_estimate=0,
            tokens_saved=0,
            warning=None,
        )

    # Compute shared weights
    timestamps = [c.last_modified for c in candidates]
    oldest, newest = min(timestamps), max(timestamps)
    freq_map = _symbol_frequencies(candidates, conn)

    scored: list[ScoredNode] = []
    for c in candidates:
        cw = _call_weight(c.depth, c.edge_type)
        rw = _recency_weight(c.last_modified, oldest, newest)
        tw = _test_coverage_weight(c.node_id, conn)
        fw = freq_map.get(c.node_id, 0.0)

        score = round(0.40 * cw + 0.25 * rw + 0.20 * tw + 0.15 * fw, 6)
        scored.append(ScoredNode(
            candidate=c,
            score=score,
            call_weight=cw,
            recency_weight=rw,
            test_coverage_weight=tw,
            symbol_frequency=fw,
        ))

    # Sort: score descending, file_path ascending as tiebreaker
    scored.sort(key=lambda s: (-s.score, s.candidate.file_path))

    # Entry nodes always first
    entries = [s for s in scored if s.candidate.is_entry]
    non_entries = [s for s in scored if not s.candidate.is_entry]
    ordered = entries + non_entries

    # Check budget-too-low: the single highest-scoring entry node alone exceeds budget
    max_entry_tokens = max((s.candidate.token_count for s in entries), default=0)
    if max_entry_tokens > budget and entries:
        total_candidate_tokens = sum(s.candidate.token_count for s in scored)
        # Include only entries that fit; if none fit, include the first one anyway
        fit_entries = [s for s in entries if s.candidate.token_count <= budget] or [entries[0]]
        entry_tokens = sum(s.candidate.token_count for s in fit_entries)
        return BundleResult(
            included=fit_entries,
            excluded_budget=non_entries,
            token_estimate=entry_tokens,
            tokens_saved=max(0, total_candidate_tokens - entry_tokens),
            warning=_WARNING_BUDGET_LOW,
        )

    # Greedy inclusion
    included: list[ScoredNode] = []
    excluded_budget: list[ScoredNode] = []
    token_total = 0

    for s in ordered:
        if token_total + s.candidate.token_count <= budget:
            included.append(s)
            token_total += s.candidate.token_count
        else:
            excluded_budget.append(s)

    total_candidate_tokens = sum(s.candidate.token_count for s in scored)
    tokens_saved = max(0, total_candidate_tokens - token_total)

    warning: str | None = None
    if not excluded_budget:
        warning = _WARNING_ALL_FIT
    elif excluded_budget:
        warning = _WARNING_MORE_EXIST.format(n=len(excluded_budget))

    return BundleResult(
        included=included,
        excluded_budget=excluded_budget,
        token_estimate=token_total,
        tokens_saved=tokens_saved,
        warning=warning,
    )
