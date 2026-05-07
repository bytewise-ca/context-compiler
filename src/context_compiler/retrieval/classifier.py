"""Task classifier — keyword signal scoring, no LLM calls.

Classifies a natural language task string into BUG_FIX, NEW_FEATURE, or REFACTOR
and returns a confidence score (0.0–1.0).
"""

from __future__ import annotations

from context_compiler.models import TaskType

# Signal keyword sets per spec FR-02.3 / FR-02.4 / FR-02.5
_SIGNALS: dict[TaskType, frozenset[str]] = {
    TaskType.BUG_FIX: frozenset({
        "fix", "bug", "error", "broken", "failing", "crash",
        "issue", "regression", "wrong", "incorrect",
    }),
    TaskType.NEW_FEATURE: frozenset({
        "add", "implement", "create", "build", "new",
        "support", "introduce", "enable",
    }),
    TaskType.REFACTOR: frozenset({
        "rename", "extract", "restructure", "move", "clean",
        "refactor", "reorganise", "decouple",
    }),
}

# All signal words — used by entry node matching to strip them from queries
ALL_SIGNAL_WORDS: frozenset[str] = frozenset().union(*_SIGNALS.values())

_STOP_WORDS: frozenset[str] = frozenset({
    # Articles / prepositions / conjunctions
    "a", "an", "the", "in", "for", "to", "of", "and", "or", "is",
    "it", "its", "on", "at", "by", "with", "from", "into", "that",
    "this", "be", "as", "are", "was", "were", "has", "have", "had",
    "do", "does", "did", "so", "but", "if", "not", "no", "up",
    # Question / relative words
    "when", "where", "which", "what", "who", "how", "why", "while",
    "then", "than", "there", "their", "they", "them", "these", "those",
    # Modal / auxiliary verbs
    "can", "could", "will", "would", "should", "may", "might", "must",
    "shall", "need", "ought",
    # Common verbs that never appear as code symbols
    "become", "becomes", "became", "accept", "accepts", "accepted",
    "receive", "receives", "received", "regarding", "subsequently",
    "existing", "related", "about", "already", "currently", "properly",
    # Quantity / determiner words
    "any", "some", "all", "both", "each", "every", "only", "also",
    "even", "just", "still", "yet", "now", "here", "thus", "since",
    "after", "before", "during", "between", "through", "without",
    "over", "under", "per", "via", "much", "more", "most",
})


class ClassificationResult:
    __slots__ = ("task_type", "confidence", "low_confidence", "scores")

    def __init__(
        self,
        task_type: TaskType,
        confidence: float,
        low_confidence: bool,
        scores: dict[TaskType, int],
    ) -> None:
        self.task_type = task_type
        self.confidence = confidence
        self.low_confidence = low_confidence
        self.scores = scores


def _tokenise(text: str) -> list[str]:
    """Lowercase and split on non-alphanumeric characters."""
    import re
    return re.findall(r"[a-z0-9]+", text.lower())


def classify(task: str) -> ClassificationResult:
    """
    Classify a task string into a TaskType with confidence score.

    Returns:
        ClassificationResult with task_type, confidence (0.0–1.0),
        low_confidence flag, and raw signal scores per type.
    """
    tokens = set(_tokenise(task))

    scores: dict[TaskType, int] = {
        task_type: len(tokens & signals)
        for task_type, signals in _SIGNALS.items()
    }

    total = sum(scores.values())

    if total == 0:
        # No signals found — default to BUG_FIX with zero confidence
        return ClassificationResult(
            task_type=TaskType.BUG_FIX,
            confidence=0.0,
            low_confidence=True,
            scores=scores,
        )

    # Winner is the task type with the most signal hits
    winner = max(scores, key=lambda t: (scores[t], t.value))  # score, then name as tiebreak
    confidence = round(scores[winner] / total, 4)
    low_confidence = confidence < 0.5

    return ClassificationResult(
        task_type=winner,
        confidence=confidence,
        low_confidence=low_confidence,
        scores=scores,
    )


def extract_query_keywords(task: str) -> list[str]:
    """
    Extract keywords for entry node matching by stripping stop words
    and task-type signal words from the task string.

    Returns an ordered list of remaining tokens (preserving first-occurrence order).
    """
    tokens = _tokenise(task)
    excluded = _STOP_WORDS | ALL_SIGNAL_WORDS
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token not in excluded and token not in seen and len(token) > 1:
            seen.add(token)
            result.append(token)
    return result
