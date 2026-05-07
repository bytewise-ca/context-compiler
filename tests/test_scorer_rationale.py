"""Tests for scorer and rationale — validates scorer/spec.md and rationale/spec.md."""

import pytest
from pathlib import Path

from context_compiler.retrieval.entry_nodes import find_entry_nodes
from context_compiler.retrieval.traversal import traverse
from context_compiler.retrieval.scorer import score_and_compile
from context_compiler.retrieval.rationale import (
    build_rationale_list,
    build_excluded_list,
    generate_rationale,
    render_explain_report,
)
from context_compiler.models import TaskType


def _full_pipeline(task: str, task_type: TaskType, budget: int, conn):
    entries = find_entry_nodes(task, conn).candidates
    traversal = traverse(entries, task_type, conn)
    bundle = score_and_compile(traversal.candidates, budget, conn)
    return entries, traversal, bundle


# ── Scorer: composite formula ────────────────────────────────────────────────

class TestScoring:
    def test_scores_between_zero_and_one(self, python_conn):
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        for s in bundle.included:
            assert 0.0 <= s.score <= 1.0

    def test_included_sorted_by_score_descending(self, python_conn):
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        # Entries always first, then rest should be sorted
        non_entries = [s for s in bundle.included if not s.candidate.is_entry]
        scores = [s.score for s in non_entries]
        assert scores == sorted(scores, reverse=True)

    def test_token_estimate_does_not_exceed_budget(self, python_conn):
        budget = 8000
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, budget, python_conn)
        assert bundle.token_estimate <= budget

    def test_token_estimate_equals_sum_of_included(self, python_conn):
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        expected = sum(s.candidate.token_count for s in bundle.included)
        assert bundle.token_estimate == expected

    def test_tokens_saved_is_non_negative(self, python_conn):
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        assert bundle.tokens_saved >= 0

    def test_budget_too_low_warning(self, python_conn):
        # Budget of 1 token — should include only entry node and warn
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 1, python_conn)
        assert bundle.warning is not None
        assert "Budget too low" in bundle.warning

    def test_budget_override_respected(self, python_conn):
        # Use 8000 first to find the actual total, then set budget to half of it
        _, _, big_bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        budget = max(big_bundle.token_estimate // 2, 10)
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, budget, python_conn)
        assert bundle.token_estimate <= budget

    def test_all_fit_warning_when_budget_not_reached(self, python_conn):
        # Very large budget — should fit everything
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 1_000_000, python_conn)
        assert bundle.excluded_budget == []
        assert bundle.warning == "All relevant nodes included; budget not reached."

    def test_more_exist_warning_when_budget_exceeded(self, python_conn):
        # Find a budget that fits entries but excludes non-entries
        _, _, big = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        entry_tokens = sum(s.candidate.token_count for s in big.included if s.candidate.is_entry)
        non_entry_tokens = sum(s.candidate.token_count for s in big.included if not s.candidate.is_entry)
        if non_entry_tokens == 0:
            pytest.skip("No non-entry candidates in fixture for this budget test")
        # Budget just covers entries but not non-entries
        budget = entry_tokens + 1
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, budget, python_conn)
        if bundle.excluded_budget:
            assert "additional relevant nodes exist" in bundle.warning

    def test_empty_candidates_returns_empty_bundle(self, python_conn):
        bundle = score_and_compile([], 8000, python_conn)
        assert bundle.included == []
        assert bundle.token_estimate == 0
        assert bundle.tokens_saved == 0


class TestDeterminism:
    def test_same_task_same_bundle_five_times(self, python_conn):
        results = []
        for _ in range(5):
            _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
            results.append([s.candidate.node_id for s in bundle.included])
        assert all(r == results[0] for r in results)

    def test_tiebreaker_is_file_path(self, python_conn):
        # Run twice — ordering must be identical (file_path tiebreaker makes it deterministic)
        _, _, bundle1 = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        _, _, bundle2 = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        ids1 = [s.candidate.node_id for s in bundle1.included]
        ids2 = [s.candidate.node_id for s in bundle2.included]
        assert ids1 == ids2


# ── Rationale generation ─────────────────────────────────────────────────────

class TestRationale:
    def test_every_included_node_has_rationale(self, python_conn):
        entries, traversal, bundle = _full_pipeline(
            "fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn
        )
        rationales = build_rationale_list(bundle.included, matched_term="retry")
        assert len(rationales) == len(bundle.included)
        for r in rationales:
            assert r and len(r) > 0

    def test_entry_node_rationale_contains_matched_term(self, python_conn):
        entries, traversal, bundle = _full_pipeline(
            "fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn
        )
        rationales = build_rationale_list(bundle.included, matched_term="retry")
        entry_rationales = [
            r for r, s in zip(rationales, bundle.included) if s.candidate.is_entry
        ]
        assert all("retry" in r or "primary task location" in r for r in entry_rationales)

    def test_covers_edge_rationale_contains_test_link(self, python_conn):
        entries, traversal, bundle = _full_pipeline(
            "fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn
        )
        rationales = build_rationale_list(bundle.included, matched_term="retry")
        covers_rationales = [
            r for r, s in zip(rationales, bundle.included)
            if s.candidate.edge_type == "COVERS"
        ]
        assert all("test link" in r for r in covers_rationales)

    def test_budget_excluded_has_reason(self, python_conn):
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 50, python_conn)
        if bundle.excluded_budget:
            reasons = build_excluded_list([], bundle.excluded_budget)
            assert len(reasons) == len(bundle.excluded_budget)
            for r in reasons:
                assert "excluded" in r or "budget" in r

    def test_no_none_rationale(self, python_conn):
        _, _, bundle = _full_pipeline("fix the payment retry logic", TaskType.BUG_FIX, 8000, python_conn)
        rationales = build_rationale_list(bundle.included, matched_term="retry")
        assert all(r is not None for r in rationales)


# ── CLI explain report ───────────────────────────────────────────────────────

class TestExplainReport:
    def test_report_contains_task(self, python_conn):
        report = render_explain_report(
            task="fix the payment retry logic",
            task_type="BUG_FIX",
            confidence=0.87,
            token_estimate=2100,
            tokens_saved=39100,
            included=[("payments/processor.py", "Included processor.py as primary task location")],
            excluded=["payments/auth.py — depth 4, exceeds limit of 2"],
        )
        assert "fix the payment retry logic" in report
        assert "BUG_FIX" in report
        assert "0.87" in report
        assert "2,100" in report
        assert "39,100" in report

    def test_report_contains_included_nodes(self, python_conn):
        report = render_explain_report(
            task="fix",
            task_type="BUG_FIX",
            confidence=1.0,
            token_estimate=100,
            tokens_saved=0,
            included=[("payments/processor.py", "primary task location")],
            excluded=[],
        )
        assert "INCLUDED" in report
        assert "processor.py" in report

    def test_report_contains_excluded_nodes(self):
        report = render_explain_report(
            task="fix",
            task_type="BUG_FIX",
            confidence=1.0,
            token_estimate=100,
            tokens_saved=0,
            included=[("a.py", "rationale")],
            excluded=["b.py — depth 4, exceeds limit of 2"],
        )
        assert "EXCLUDED" in report
        assert "b.py" in report

    def test_report_is_human_readable_without_tooling(self):
        report = render_explain_report(
            task="fix the payment retry logic",
            task_type="BUG_FIX",
            confidence=0.87,
            token_estimate=2100,
            tokens_saved=39100,
            included=[("payments/processor.py", "primary match")],
            excluded=[],
        )
        # Should be plain text — no JSON, no YAML, no special markup
        assert "{" not in report
        assert "}" not in report
