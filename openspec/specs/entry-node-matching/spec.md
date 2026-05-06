# entry-node-matching Specification

## Purpose
Given a task string, identify the top-K graph nodes most likely to be the starting point for BFS traversal. This is the highest-risk component of the system — poor matching produces irrelevant bundles even when traversal and scoring are correct. The solution uses BM25 over a tokenised symbol corpus as the primary retrieval method, with a local embedding model (fastembed) as a semantic fallback when BM25 returns low-confidence results.

## Requirements

### Requirement: Symbol corpus tokenisation
The system SHALL tokenise all symbol names and file paths in the graph at index time using the following rules:
- camelCase split: `PaymentProcessor` → `["payment", "processor"]`
- snake_case split: `process_with_retry` → `["process", "with", "retry"]`
- Path segment split: `payments/notifier.py` → `["payments", "notifier"]`
- All tokens lowercased

### Requirement: BM25 index construction
The system SHALL build a BM25 index over the tokenised symbol corpus at index time. Each node's BM25 document SHALL be the concatenation of its tokenised symbol name and tokenised file path segments.

### Requirement: Query keyword extraction
The system SHALL extract query keywords from the task string by:
- Tokenising the task string
- Removing common English stop words
- Removing task-type signal words (`fix`, `add`, `rename`, `implement`, etc.)
- Returning the remaining tokens as the query

### Requirement: BM25 retrieval
The system SHALL query the BM25 index with the extracted keywords and return the top-K candidates (default K=5) with their scores. Nodes with a score of 0 SHALL be excluded.

### Requirement: Confidence scoring
The system SHALL compute a normalised confidence score (0.0–1.0) for the top result by dividing its BM25 score by the maximum score in the result set.

### Requirement: Fuzzy fallback
When BM25 returns fewer than 2 candidates with score > 0, the system SHALL apply fuzzy string matching (via `rapidfuzz`) between each query keyword and all symbol names, using a minimum similarity threshold of 70. Fuzzy matches above threshold SHALL be added to the candidate set.

### Requirement: Semantic fallback (optional dependency)
When the `fastembed` package is installed, the system SHALL also run a semantic embedding query using `BAAI/bge-small-en-v1.5` (23MB, downloaded once to `~/.cache/fastembed` on first use). Semantic results SHALL be merged with BM25 results using Reciprocal Rank Fusion (RRF).

### Requirement: Multi-entry BFS input
The system SHALL return the top-K candidate nodes (not just top-1) as entry points. The traversal layer SHALL run BFS from all K entry nodes and merge results. A node reached from multiple entry points SHALL receive a higher composite score.

### Requirement: Empty result handling
When no candidates are found after BM25, fuzzy, and (if available) semantic search, the system SHALL return an empty candidate list. The caller SHALL handle this by returning an empty bundle with an actionable message.

### Requirement: Zero LLM calls
The system SHALL perform entry node matching without any external LLM API calls. The optional fastembed model is a static local model, not an LLM API.

### Requirement: Performance
Entry node matching SHALL complete within 50ms for repositories up to 50,000 nodes when using BM25 only. With semantic fallback enabled, matching SHALL complete within 150ms.

### Requirement: Documented limitation
The system documentation SHALL explicitly state that task strings using terms that do not appear in symbol names or file paths (semantic gaps) may return low-confidence or empty results in the BM25-only configuration. The semantic fallback partially addresses this limitation.

## Scenarios

#### Scenario: Exact keyword match in symbol name
- GIVEN a graph containing the node `payments/processor.py::PaymentProcessor`
- AND the task string `"fix the payment retry logic"`
- WHEN entry node matching runs (keywords: `["payment", "retry", "logic"]`)
- THEN `processor.py` and `notifier.py` nodes are in the top-K results
- AND confidence is ≥ 0.5

#### Scenario: camelCase symbol matched by task keyword
- GIVEN a graph containing the node `auth/oauth_manager.py::OAuthManager`
- AND the task string `"add OAuth token refresh"`
- WHEN entry node matching runs (keywords: `["oauth", "token", "refresh"]`)
- THEN `OAuthManager` node is in the top-K results

#### Scenario: No keyword match — fuzzy fallback fires
- GIVEN a graph with no symbol containing the exact string "notifier"
- AND a node named `payments/notify_handler.py::NotifyHandler`
- AND the task string `"fix the notifier logic"`
- WHEN BM25 returns 0 results
- THEN fuzzy matching finds `NotifyHandler` with similarity ≥ 70
- AND that node is returned as a candidate

#### Scenario: Semantic gap with fastembed installed
- GIVEN `fastembed` is installed
- AND the graph has `auth/authenticate.py::authenticate_user` but no symbol containing "login"
- AND the task string `"fix the login flow"`
- WHEN BM25 returns 0 results and fuzzy fallback returns 0 results
- THEN the semantic embedding query returns `authenticate_user` as a top candidate
- AND the result is returned with `low_confidence: true`

#### Scenario: Completely unrecognisable task string
- GIVEN the task string `"make it work better"`
- WHEN keyword extraction runs
- THEN the keyword list is empty after stop word and signal removal
- AND the system returns an empty candidate list immediately
- AND no BM25, fuzzy, or embedding query is executed

#### Scenario: Multi-entry produces better coverage
- GIVEN a task string matching both `processor.py` and `notifier.py` as top-2 candidates
- WHEN BFS runs from both entry nodes
- THEN the merged result set includes nodes reachable from either entry point
- AND nodes reachable from both receive a higher composite score

#### Scenario: Performance within budget
- GIVEN a graph with 50,000 nodes
- AND a task string with 4 keywords
- WHEN BM25 matching runs (no semantic fallback)
- THEN results are returned within 50ms
