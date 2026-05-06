"""Tests for the classifier component — validates against classifier/spec.md scenarios."""

import pytest
from context_compiler.retrieval.classifier import classify, extract_query_keywords
from context_compiler.models import TaskType


class TestClassification:
    def test_clear_bug_fix(self):
        result = classify("fix the payment retry logic")
        assert result.task_type == TaskType.BUG_FIX
        assert result.confidence >= 0.5
        assert not result.low_confidence

    def test_clear_new_feature(self):
        result = classify("add OAuth token refresh support to the auth module")
        assert result.task_type == TaskType.NEW_FEATURE
        assert result.confidence >= 0.5
        assert not result.low_confidence

    def test_clear_refactor(self):
        result = classify("extract the retry logic into a base class")
        assert result.task_type == TaskType.REFACTOR
        assert result.confidence >= 0.5
        assert not result.low_confidence

    def test_vague_task_defaults_to_bug_fix(self):
        result = classify("make it work better")
        assert result.task_type == TaskType.BUG_FIX
        assert result.confidence == 0.0
        assert result.low_confidence

    def test_vague_task_empty_string(self):
        result = classify("")
        assert result.task_type == TaskType.BUG_FIX
        assert result.confidence == 0.0
        assert result.low_confidence

    def test_ambiguous_task_returns_without_crash(self):
        result = classify("add a fix for the broken retry logic")
        assert result.task_type in (TaskType.BUG_FIX, TaskType.NEW_FEATURE, TaskType.REFACTOR)
        assert 0.0 <= result.confidence <= 1.0

    def test_confidence_range(self):
        for task in [
            "fix the login bug",
            "implement new dashboard",
            "refactor the payment module",
            "make it work",
        ]:
            result = classify(task)
            assert 0.0 <= result.confidence <= 1.0

    def test_low_confidence_flag_below_threshold(self):
        # Single signal word against competing signals — may be low confidence
        result = classify("fix and add and refactor")
        assert 0.0 <= result.confidence <= 1.0
        if result.confidence < 0.5:
            assert result.low_confidence

    def test_scores_dict_present(self):
        result = classify("fix the bug")
        assert TaskType.BUG_FIX in result.scores
        assert TaskType.NEW_FEATURE in result.scores
        assert TaskType.REFACTOR in result.scores

    def test_bug_fix_signals_individually(self):
        for signal in ["fix", "bug", "error", "broken", "failing", "crash",
                       "issue", "regression", "wrong", "incorrect"]:
            result = classify(f"the {signal} in module")
            assert result.task_type == TaskType.BUG_FIX

    def test_new_feature_signals_individually(self):
        for signal in ["add", "implement", "create", "build", "support", "introduce", "enable"]:
            result = classify(f"{signal} the feature in module")
            assert result.task_type == TaskType.NEW_FEATURE

    def test_refactor_signals_individually(self):
        for signal in ["rename", "extract", "restructure", "move", "clean",
                       "refactor", "reorganise", "decouple"]:
            result = classify(f"{signal} the module")
            assert result.task_type == TaskType.REFACTOR


class TestDeterminism:
    def test_same_task_returns_same_result_five_times(self):
        task = "fix the payment retry logic"
        results = [classify(task) for _ in range(5)]
        assert all(r.task_type == results[0].task_type for r in results)
        assert all(r.confidence == results[0].confidence for r in results)
        assert all(r.low_confidence == results[0].low_confidence for r in results)

    def test_determinism_for_vague_task(self):
        task = "make it work better"
        results = [classify(task) for _ in range(5)]
        assert all(r.task_type == results[0].task_type for r in results)
        assert all(r.confidence == results[0].confidence for r in results)


class TestKeywordExtraction:
    def test_strips_task_signals(self):
        keywords = extract_query_keywords("fix the payment retry logic")
        assert "fix" not in keywords
        assert "payment" in keywords
        assert "retry" in keywords

    def test_strips_stop_words(self):
        keywords = extract_query_keywords("fix the payment retry logic")
        assert "the" not in keywords
        assert "in" not in keywords

    def test_strips_all_signal_types(self):
        keywords = extract_query_keywords("add refactor fix rename auth module")
        assert "add" not in keywords
        assert "refactor" not in keywords
        assert "fix" not in keywords
        assert "rename" not in keywords
        assert "auth" in keywords
        assert "module" in keywords

    def test_empty_task_returns_empty_list(self):
        assert extract_query_keywords("") == []

    def test_only_signals_returns_empty_list(self):
        assert extract_query_keywords("fix add refactor the") == []

    def test_no_duplicates_in_output(self):
        keywords = extract_query_keywords("retry retry retry handler")
        assert keywords.count("retry") == 1

    def test_preserves_meaningful_terms(self):
        keywords = extract_query_keywords("extract the retry logic into a base class")
        # "extract" is a signal, "retry", "logic", "base", "class" should remain
        assert "retry" in keywords
        assert "logic" in keywords
        assert "base" in keywords
