"""Entry node matching — BM25 primary, fuzzy fallback, optional semantic via fastembed.

Given a task string, returns the top-K graph nodes most likely to be
the starting point for BFS traversal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import kuzu
from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz, process as fuzz_process

from context_compiler.retrieval.classifier import extract_query_keywords

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenise_symbol(text: str) -> list[str]:
    """Split camelCase, snake_case, path separators into lowercase tokens."""
    # camelCase → camel Case
    text = _CAMEL_RE.sub(" ", text)
    # Replace separators
    text = re.sub(r"[_\-/\\.]+", " ", text)
    return [t.lower() for t in text.split() if len(t) > 1]


def _tokenise_docstring(text: str) -> list[str]:
    """Tokenise natural language docstring text into lowercase words (length > 2)."""
    words = re.findall(r"[a-zA-Z]+", text)
    return [w.lower() for w in words if len(w) > 2]


@dataclass
class EntryNode:
    node_id: str
    file_path: str
    symbol_name: str | None
    symbol_type: str
    bm25_score: float
    confidence: float  # normalised 0.0–1.0


@dataclass
class EntryMatchResult:
    candidates: list[EntryNode]
    keywords: list[str]
    confidence: float          # top candidate confidence, 0.0 if empty
    used_fuzzy: bool = False
    used_semantic: bool = False


def _load_nodes(conn: kuzu.Connection) -> list[dict]:
    """Load all non-placeholder nodes from the graph."""
    result = conn.execute(
        "MATCH (n:Node) "
        "WHERE NOT n.id STARTS WITH '__' "
        "RETURN n.id, n.file_path, n.symbol_name, n.symbol_type, n.docstring"
    )
    nodes = []
    while result.has_next():
        row = result.get_next()
        nodes.append({
            "id": row[0],
            "file_path": row[1],
            "symbol_name": row[2],
            "symbol_type": row[3],
            "docstring": row[4] or "",
        })
    return nodes


def _build_corpus(nodes: list[dict]) -> list[list[str]]:
    """Build tokenised BM25 corpus — one document per node.

    Each document = tokenised file_path + symbol_name + docstring first line.
    Docstring tokens let BM25 match domain language that doesn't appear in symbol names.
    """
    corpus = []
    for node in nodes:
        tokens = _tokenise_symbol(node["file_path"])
        if node["symbol_name"]:
            tokens += _tokenise_symbol(node["symbol_name"])
        if node["docstring"]:
            tokens += _tokenise_docstring(node["docstring"])
        corpus.append(tokens)
    return corpus


def find_entry_nodes(
    task: str,
    conn: kuzu.Connection,
    top_k: int = 5,
    fuzzy_threshold: int = 70,
) -> EntryMatchResult:
    """
    Find the top-K entry nodes for a task string.

    Pipeline:
      1. Extract keywords from task string
      2. BM25 query over tokenised symbol corpus
      3. Fuzzy fallback if BM25 returns < 2 results
      4. Semantic fallback via fastembed if installed and still < 2 results
    """
    keywords = extract_query_keywords(task)

    if not keywords:
        return EntryMatchResult(candidates=[], keywords=[], confidence=0.0)

    nodes = _load_nodes(conn)
    if not nodes:
        return EntryMatchResult(candidates=[], keywords=keywords, confidence=0.0)

    corpus = _build_corpus(nodes)
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(keywords)

    # Collect positive-scoring candidates above a minimum threshold.
    # The threshold prevents a single weak BM25 match (e.g. a common English word
    # that rarely appears in code) from blocking the fuzzy/semantic fallback.
    _BM25_MIN_SCORE = 0.5
    scored = sorted(
        ((score, i) for i, score in enumerate(scores) if score >= _BM25_MIN_SCORE),
        reverse=True,
    )[:top_k]

    used_fuzzy = False
    used_semantic = False

    if len(scored) < 2:
        # Fuzzy fallback
        symbol_texts = [
            (node["symbol_name"] or "") + " " + node["file_path"]
            for node in nodes
        ]
        fuzzy_hits: set[int] = set(i for _, i in scored)

        for kw in keywords:
            matches = fuzz_process.extract(
                kw,
                symbol_texts,
                scorer=fuzz.partial_ratio,
                limit=top_k,
            )
            for text, sim, idx in matches:
                if sim >= fuzzy_threshold and idx not in fuzzy_hits:
                    fuzzy_hits.add(idx)
                    # Assign a synthetic score lower than any BM25 score
                    scored.append((sim / 1000.0, idx))

        used_fuzzy = True
        scored = sorted(scored, reverse=True)[:top_k]

    if len(scored) < 2:
        # Semantic fallback — only if fastembed is installed
        semantic_candidates = _semantic_search(keywords, nodes, top_k, exclude={i for _, i in scored})
        if semantic_candidates:
            used_semantic = True
            scored = sorted(scored + semantic_candidates, reverse=True)[:top_k]

    if not scored:
        return EntryMatchResult(candidates=[], keywords=keywords, confidence=0.0)

    max_score = scored[0][0]
    confidence = round(scored[0][0] / max_score, 4) if max_score > 0 else 0.0

    candidates = []
    for score, idx in scored:
        node = nodes[idx]
        norm_confidence = round(score / max_score, 4) if max_score > 0 else 0.0
        candidates.append(EntryNode(
            node_id=node["id"],
            file_path=node["file_path"],
            symbol_name=node["symbol_name"] or None,
            symbol_type=node["symbol_type"],
            bm25_score=score,
            confidence=norm_confidence,
        ))

    return EntryMatchResult(
        candidates=candidates,
        keywords=keywords,
        confidence=confidence,
        used_fuzzy=used_fuzzy,
        used_semantic=used_semantic,
    )


def _semantic_search(
    keywords: list[str],
    nodes: list[dict],
    top_k: int,
    exclude: set[int],
) -> list[tuple[float, int]]:
    """Semantic search via fastembed (optional dependency). Returns [] if not installed."""
    try:
        import numpy as np
        from fastembed import TextEmbedding
    except ImportError:
        return []

    model = _get_embedding_model()
    if model is None:
        return []

    # Embed node documents (file path + symbol name + docstring)
    docs = [
        _tokenise_symbol(n["file_path"])
        + _tokenise_symbol(n["symbol_name"] or "")
        + _tokenise_docstring(n.get("docstring", ""))
        for n in nodes
    ]
    doc_texts = [" ".join(tokens) for tokens in docs]
    query_text = " ".join(keywords)

    doc_embeddings = np.array(list(model.embed(doc_texts)))
    query_embedding = np.array(list(model.embed([query_text])))[0]

    # Cosine similarity (fastembed normalises vectors)
    sims = doc_embeddings @ query_embedding

    results = []
    for idx in np.argsort(sims)[::-1][:top_k]:
        if idx not in exclude and sims[idx] > 0.3:
            results.append((float(sims[idx]) * 0.5, int(idx)))  # scale below BM25

    return results


_embedding_model = None


def _get_embedding_model():
    """Lazy-load the fastembed model. Returns None if fastembed is not installed."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    try:
        from fastembed import TextEmbedding
        _embedding_model = TextEmbedding("BAAI/bge-small-en-v1.5")
        return _embedding_model
    except Exception:
        return None
