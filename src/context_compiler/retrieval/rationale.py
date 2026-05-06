"""Template-based rationale generation — no LLM calls.

Generates one-line inclusion rationale per node and exclusion reasons
for depth-exceeded and budget-exhausted candidates.
"""

from __future__ import annotations

from context_compiler.retrieval.scorer import ScoredNode
from context_compiler.retrieval.traversal import CandidateNode

# Inclusion templates (per spec FR-05)
_TEMPLATES = {
    "ENTRY":      "Included {node} as primary task location (matched '{term}')",
    "CALLS":      "Included {node} because it is called by {source} (depth {d})",
    "IMPORTS":    "Included {node} because {source} imports it (depth {d})",
    "COVERS":     "Included {node} because it covers {source} (test link)",
    "DEFINED_IN": "Included {node} because it is defined in the same module as {source} (pattern reference)",
}


def _node_label(candidate: CandidateNode) -> str:
    """Short human-readable label: symbol name if available, else file path."""
    if candidate.symbol_name:
        return candidate.symbol_name
    return candidate.file_path


def generate_rationale(scored_node: ScoredNode, matched_term: str = "") -> str:
    """Generate a one-line rationale string for an included node."""
    c = scored_node.candidate
    label = _node_label(c)

    if c.edge_type == "ENTRY":
        return _TEMPLATES["ENTRY"].format(node=label, term=matched_term or label)

    source_label = c.source_id or "unknown"
    # Simplify source_id to a readable label (strip file::symbol → symbol or file)
    if "::" in source_label:
        parts = source_label.split("::")
        source_label = parts[-1] if parts[-1] else parts[0]
    elif "/" in source_label:
        source_label = source_label.split("/")[-1]

    template = _TEMPLATES.get(c.edge_type, _TEMPLATES["CALLS"])
    return template.format(node=label, source=source_label, d=c.depth)


def generate_exclusion_reason_depth(node_id: str, file_path: str, reason: str) -> str:
    """Exclusion reason for a depth-exceeded node."""
    label = file_path.split("/")[-1] if "/" in file_path else file_path
    return f"{label} — {reason}"


def generate_exclusion_reason_budget(scored_node: ScoredNode) -> str:
    """Exclusion reason for a budget-exhausted node."""
    label = _node_label(scored_node.candidate)
    tokens = scored_node.candidate.token_count
    return f"{label} — excluded: token budget exhausted ({tokens:,} tokens)"


def build_rationale_list(
    included: list[ScoredNode],
    matched_term: str = "",
) -> list[str]:
    """Return one rationale string per included node, in the same order."""
    return [generate_rationale(s, matched_term) for s in included]


def build_excluded_list(
    depth_excluded: list[dict],
    budget_excluded: list[ScoredNode],
) -> list[str]:
    """Return exclusion reason strings for all excluded candidates."""
    reasons = []
    for item in depth_excluded:
        reasons.append(generate_exclusion_reason_depth(
            item["node_id"], item["file_path"], item["reason"]
        ))
    for s in budget_excluded:
        reasons.append(generate_exclusion_reason_budget(s))
    return reasons


def render_explain_report(
    task: str,
    task_type: str,
    confidence: float,
    token_estimate: int,
    tokens_saved: int,
    included: list[tuple[str, str]],    # (file_path, rationale)
    excluded: list[str],
) -> str:
    """Render the human-readable CLI explain report."""
    lines = [
        f'Task:        "{task}"',
        f"Task type:   {task_type}  (confidence: {confidence:.2f})",
        f"Token est:   {token_estimate:,} tokens  (saved {tokens_saved:,} vs full context)",
        "",
        f"INCLUDED ({len(included)} nodes):",
    ]
    for fp, rationale in included:
        lines.append(f"  ✓ {fp:<45} — {rationale}")

    if excluded:
        lines.append("")
        lines.append("EXCLUDED:")
        for reason in excluded:
            lines.append(f"  ✗ {reason}")

    return "\n".join(lines)
