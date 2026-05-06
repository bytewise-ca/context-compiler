# scorer Specification

## Purpose
Assign a composite relevance score to each candidate node collected by traversal, rank nodes by score, and select nodes greedily into the context bundle until the token budget is exhausted. Scoring is fully deterministic — given the same graph state and candidate set, the output order is always identical.

## Requirements

### Requirement: Composite scoring formula
The system SHALL score each candidate node using:
```
score = 0.40 × call_weight
      + 0.25 × recency_weight
      + 0.20 × test_coverage_weight
      + 0.15 × symbol_frequency
```

### Requirement: call_weight calculation
`call_weight` SHALL be inversely proportional to BFS depth:
- depth 1 → 1.0
- depth 2 → 0.6
- depth 3 → 0.3
- depth > 3 → 0.1

### Requirement: recency_weight calculation
`recency_weight` SHALL be computed as:
```
recency_weight = (node.last_modified - oldest_in_repo) / (newest_in_repo - oldest_in_repo)
```
A node modified at the same time as the most recently modified file in the repo receives a weight of 1.0. The oldest file receives 0.0.

### Requirement: test_coverage_weight calculation
`test_coverage_weight` SHALL be 1.0 for nodes that have at least one `COVERS` edge pointing to them, and 0.0 for all other nodes.

### Requirement: symbol_frequency calculation
`symbol_frequency` SHALL be normalised: the node referenced by the most other files receives 1.0; a node referenced by no other files receives 0.0. Formula:
```
symbol_frequency = reference_count / max_reference_count_in_repo
```

### Requirement: Entry node score
The entry node SHALL always receive the highest score and SHALL always be included in the bundle, even if its token count alone reaches the budget.

### Requirement: Deterministic ranking
The system SHALL sort candidate nodes by score descending. When two nodes have equal scores, the system SHALL use file path as a lexicographic tiebreaker (ascending). This guarantees identical output for identical inputs.

### Requirement: Token budget enforcement
The system SHALL include nodes in score order until adding the next node would exceed the token budget. The token budget SHALL never be exceeded. Partial inclusion of a node (i.e., truncating file content) is NOT permitted.

### Requirement: Default token budget
The default token budget SHALL be 8,000 tokens, configurable via the environment variable `CC_TOKEN_BUDGET` or the `budget` parameter passed to `get_context`.

### Requirement: Token estimate calculation
Token count for a node SHALL be estimated as `ceil(character_count / 4)`. This estimate SHALL be computed at index time and stored as node metadata.

### Requirement: Tokens saved reporting
The system SHALL compute and report `tokens_saved` as the difference between the sum of token counts of all traversed candidates and the token count of the returned bundle.

### Requirement: Budget-exceeded notice
When high-scoring candidates are excluded due to budget exhaustion, the system SHALL include a notice in the bundle metadata: `"Note: {n} additional relevant nodes exist. Call get_context with a higher budget to include them."`

### Requirement: Budget too low warning
When the token budget is lower than the token count of the entry node alone, the system SHALL include only the entry node and return a warning: `"Budget too low to include dependencies. Increase budget for better context."`

## Scenarios

#### Scenario: Standard scoring selects top nodes within budget
- GIVEN 6 candidate nodes with known scores and token counts
- AND the token budget is 8,000 tokens
- WHEN scoring and selection runs
- THEN nodes are included in descending score order
- AND total token count does not exceed 8,000
- AND `tokens_saved` equals total candidate tokens minus included tokens

#### Scenario: Equal scores use file path as tiebreaker
- GIVEN two nodes with identical composite scores
- WHEN scored and ranked
- THEN the node with the lexicographically earlier file path is ranked first
- AND the result is identical on repeated calls

#### Scenario: Entry node always included
- GIVEN the entry node has a token count of 7,500
- AND the budget is 8,000
- WHEN scoring runs
- THEN the entry node is included
- AND a budget-exceeded notice is added to metadata
- AND no other nodes are included

#### Scenario: Budget too low warning
- GIVEN the budget is set to 500 tokens
- AND the entry node has a token count of 800
- WHEN scoring runs
- THEN only the entry node is included
- AND the bundle contains the warning `"Budget too low to include dependencies"`

#### Scenario: Budget override via parameter
- GIVEN `get_context` is called with `budget=16000`
- WHEN scoring runs
- THEN nodes are selected until 16,000 tokens are reached
- AND the returned bundle never exceeds 16,000 tokens

#### Scenario: All candidates fit within budget
- GIVEN total token count of all candidates is 3,200
- AND the budget is 8,000
- WHEN scoring runs
- THEN all candidates are included
- AND the bundle metadata notes `"All relevant nodes included; budget not reached"`

#### Scenario: Reproducibility
- GIVEN the same graph state and task string
- WHEN `get_context` is called 5 times
- THEN all 5 returned bundles are byte-identical
