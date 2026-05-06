# traversal Specification

## Purpose
Given one or more entry nodes and a classified task type, traverse the dependency graph using BFS to collect a set of candidate nodes for the context bundle. Traversal direction and depth limits differ per task type to reflect the different information needs of bug fixes, new features, and refactors.

## Requirements

### Requirement: BFS from entry nodes
The system SHALL traverse the graph using breadth-first search (BFS) from all entry nodes returned by the entry node matching step.

### Requirement: BUG_FIX traversal strategy
For `BUG_FIX` tasks, the system SHALL:
- Traverse inbound `CALLS` edges (nodes that call the entry node), up to depth 2
- Traverse `COVERS` edges (test files that cover the entry node), no depth limit
- NOT traverse outbound `CALLS` edges as primary signal

### Requirement: NEW_FEATURE traversal strategy
For `NEW_FEATURE` tasks, the system SHALL:
- Traverse `IMPORTS` edges from the entry node (what the entry node imports), up to depth 2
- Include sibling symbols defined in the same file as the entry node
- NOT traverse inbound `CALLS` edges as primary signal

### Requirement: REFACTOR traversal strategy
For `REFACTOR` tasks, the system SHALL:
- Traverse outbound `CALLS` edges (all nodes that call the entry node, i.e., the full blast radius), up to depth 3
- Traverse ALL `COVERS` edges (every test file covering the entry node or its callers)
- Prioritise nodes by call frequency

### Requirement: No zero-path nodes
The system SHALL NOT include any node that has no structural path (via graph edges) to any entry node.

### Requirement: Multi-entry merge
When multiple entry nodes are provided, the system SHALL run BFS from each and merge the resulting node sets. If the same node is reached from multiple entry points, it SHALL be included once with its shortest path depth recorded.

### Requirement: Depth tracking
The system SHALL record the BFS depth at which each node was first reached. Depth is used by the scorer and the rationale generator.

### Requirement: Edge type tracking
The system SHALL record which edge type (`CALLS`, `IMPORTS`, `COVERS`) was used to reach each candidate node. Edge type is used by the rationale generator.

### Requirement: Performance
Graph traversal SHALL complete within 200ms for repositories up to 50,000 nodes.

## Scenarios

#### Scenario: BUG_FIX traversal collects callers and tests
- GIVEN entry node `payments/processor.py::PaymentProcessor`
- AND `notifier.py` has a `CALLS` edge to `PaymentProcessor` (depth 1)
- AND `tests/test_processor.py` has a `COVERS` edge to `PaymentProcessor`
- AND `config/payment_settings.py` is imported by `processor.py` (depth 2 via IMPORTS)
- WHEN BUG_FIX traversal runs with depth limit 2
- THEN candidates include `notifier.py` (inbound CALLS, depth 1)
- AND candidates include `test_processor.py` (COVERS edge)
- AND candidates include `payment_settings.py` (IMPORTS edge, depth 2)

#### Scenario: BUG_FIX traversal excludes nodes beyond depth limit
- GIVEN `payments/auth.py` has a `CALLS` edge to `notifier.py` (depth 4 from entry node)
- WHEN BUG_FIX traversal runs with depth limit 2
- THEN `auth.py` is NOT in the candidate set
- AND `auth.py` is recorded as excluded with reason `"depth 4, exceeds limit of 2"`

#### Scenario: NEW_FEATURE traversal collects imports and siblings
- GIVEN entry node `auth/oauth.py`
- AND `auth/oauth.py` imports `auth/token_store.py` and `utils/http_client.py`
- AND `auth/session.py` is defined in the same module directory
- WHEN NEW_FEATURE traversal runs
- THEN candidates include `token_store.py` and `http_client.py` (IMPORTS edges)
- AND candidates include `session.py` (sibling symbol)

#### Scenario: REFACTOR traversal collects full caller blast radius
- GIVEN entry node `payments/processor.py::process_with_retry`
- AND 8 different files have `CALLS` edges to `process_with_retry`
- AND 3 test files have `COVERS` edges
- WHEN REFACTOR traversal runs with depth limit 3
- THEN all 8 caller files are in the candidate set (within depth limit)
- AND all 3 test files are in the candidate set

#### Scenario: Nodes with no structural path are excluded
- GIVEN `utils/logger.py` has no edge path to the entry node
- WHEN any traversal runs
- THEN `utils/logger.py` is NOT in the candidate set

#### Scenario: Multi-entry merge de-duplicates nodes
- GIVEN two entry nodes: `processor.py` and `notifier.py`
- AND `test_processor.py` covers both
- WHEN BFS runs from both entry nodes
- THEN `test_processor.py` appears once in the merged candidate set
- AND its depth is recorded as the minimum depth from either entry node
