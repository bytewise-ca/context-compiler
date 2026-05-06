# rationale Specification

## Purpose
Generate a one-line human-readable explanation for every node included in the context bundle, and a one-line exclusion reason for every high-scoring candidate that was not included. Rationale is generated from graph edge metadata using string templates — no LLM call is made.

## Requirements

### Requirement: Rationale for every included node
The system SHALL generate exactly one rationale string for every node included in the bundle. A bundle SHALL NOT be returned with any node missing a rationale.

### Requirement: Entry node rationale template
The rationale for the entry node SHALL follow:
```
"Included {node} as primary task location (matched '{term}')"
```
where `{term}` is the query keyword that most closely matched the node's symbol name or file path.

### Requirement: CALLS edge rationale template
The rationale for a node reached via a `CALLS` edge SHALL follow:
```
"Included {node} because it is called by {source} (depth {d})"
```

### Requirement: IMPORTS edge rationale template
The rationale for a node reached via an `IMPORTS` edge SHALL follow:
```
"Included {node} because {source} imports it (depth {d})"
```

### Requirement: COVERS edge rationale template
The rationale for a node reached via a `COVERS` edge SHALL follow:
```
"Included {node} because it covers {source} (test link)"
```

### Requirement: Sibling node rationale template
The rationale for a sibling symbol (same module, NEW_FEATURE traversal) SHALL follow:
```
"Included {node} because it is defined in the same module as {source} (pattern reference)"
```

### Requirement: Exclusion reasons for depth-exceeded nodes
The system SHALL list every candidate node that was traversed but excluded due to depth limit, with the reason:
```
"{node} — depth {d}, exceeds limit of {max_depth}"
```

### Requirement: Exclusion reasons for budget-exhausted nodes
The system SHALL list every candidate node excluded due to token budget exhaustion, with the reason:
```
"{node} — excluded: token budget exhausted ({token_count} tokens)"
```

### Requirement: No LLM calls
All rationale strings SHALL be generated using string template interpolation from graph edge metadata. No LLM API call SHALL be made during rationale generation.

### Requirement: Human-readable CLI report
When called via the `explain` CLI command, the system SHALL render the rationale as a formatted plain-text report to stdout, readable without additional tooling:
```
Task:        "{task}"
Task type:   {task_type}  (confidence: {confidence})
Token est:   {token_estimate} tokens  (saved {tokens_saved} vs full context)

INCLUDED ({n} nodes):
  ✓ {file_path:<40} — {rationale}
  ...

EXCLUDED:
  ✗ {file_path:<40} — {reason}
  ...
```

## Scenarios

#### Scenario: Entry node rationale is generated
- GIVEN the entry node is `payments/processor.py::PaymentProcessor`
- AND the matching keyword was `"payment"`
- WHEN rationale is generated
- THEN the rationale string is `"Included processor.py as primary task location (matched 'payment')"`

#### Scenario: CALLS edge rationale is generated
- GIVEN `notifier.py` was reached via a `CALLS` edge from `processor.py` at depth 1
- WHEN rationale is generated
- THEN the rationale string is `"Included notifier.py because it is called by processor.py (depth 1)"`

#### Scenario: COVERS edge rationale is generated
- GIVEN `tests/test_processor.py` was reached via a `COVERS` edge pointing to `processor.py`
- WHEN rationale is generated
- THEN the rationale string is `"Included test_processor.py because it covers processor.py (test link)"`

#### Scenario: IMPORTS edge rationale is generated
- GIVEN `config/payment_settings.py` was reached via an `IMPORTS` edge at depth 2
- WHEN rationale is generated
- THEN the rationale string is `"Included payment_settings.py because processor.py imports it (depth 2)"`

#### Scenario: Depth-exceeded exclusion reason is generated
- GIVEN `payments/auth.py` was traversed but excluded because its depth (4) exceeded the limit (2)
- WHEN the excluded list is generated
- THEN the exclusion entry reads `"payments/auth.py — depth 4, exceeds limit of 2"`

#### Scenario: Budget-exhausted exclusion reason is generated
- GIVEN `utils/http_client.py` was scored but excluded because the budget was exhausted
- WHEN the excluded list is generated
- THEN the exclusion entry reads `"utils/http_client.py — excluded: token budget exhausted (1,200 tokens)"`

#### Scenario: CLI explain command renders full report
- GIVEN a task `"fix the payment retry logic"`
- WHEN the developer runs `uvx context-compiler explain --repo ./my-project --task "fix the payment retry logic"`
- THEN stdout contains the task type, confidence, token estimate, tokens saved
- AND each included node is listed with its rationale
- AND each excluded candidate is listed with its exclusion reason
- AND the output is readable in a terminal without additional tooling

#### Scenario: No node is missing a rationale
- GIVEN a bundle with 5 included nodes
- WHEN the bundle is returned
- THEN every node has a non-empty rationale string
- AND no rationale string is null or empty
