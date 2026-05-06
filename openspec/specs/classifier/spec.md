# classifier Specification

## Purpose
Classify a natural language task string into one of three task types — BUG_FIX, NEW_FEATURE, or REFACTOR — using deterministic keyword signal scoring with no LLM calls. Return a confidence score alongside the classification to enable downstream handling of ambiguous inputs.

## Requirements

### Requirement: Task type taxonomy
The system SHALL classify any task string into exactly one of three types: `BUG_FIX`, `NEW_FEATURE`, or `REFACTOR`.

### Requirement: Keyword signal sets
The system SHALL use the following keyword signal sets:
- `BUG_FIX` signals: `fix`, `bug`, `error`, `broken`, `failing`, `crash`, `issue`, `regression`, `wrong`, `incorrect`
- `NEW_FEATURE` signals: `add`, `implement`, `create`, `build`, `new`, `support`, `introduce`, `enable`
- `REFACTOR` signals: `rename`, `extract`, `restructure`, `move`, `clean`, `refactor`, `reorganise`, `decouple`

### Requirement: No LLM calls
The system SHALL perform classification using keyword scoring only. No external LLM API calls SHALL be made in the classification step.

### Requirement: Confidence score
The system SHALL return a confidence score between 0.0 and 1.0 alongside the classified task type. Confidence SHALL reflect the relative signal strength of the winning task type against competing types.

### Requirement: Low confidence default
When confidence is below 0.5, the system SHALL default to `BUG_FIX` and set `low_confidence: true` in the bundle metadata.

### Requirement: Deterministic output
Given the same task string, the system SHALL always return the same task type and confidence score.

### Requirement: Task signal stripping for keyword extraction
The system SHALL strip task-type signal words (from all three sets) and common stop words before passing remaining keywords to the entry node matching step.

## Scenarios

#### Scenario: Clear bug fix task
- GIVEN the task string `"fix the payment retry logic"`
- WHEN the classifier runs
- THEN task type is `BUG_FIX`
- AND confidence is ≥ 0.5
- AND `low_confidence` is absent or false

#### Scenario: Clear new feature task
- GIVEN the task string `"add OAuth token refresh support to the auth module"`
- WHEN the classifier runs
- THEN task type is `NEW_FEATURE`
- AND confidence is ≥ 0.5

#### Scenario: Clear refactor task
- GIVEN the task string `"extract the retry logic into a base class"`
- WHEN the classifier runs
- THEN task type is `REFACTOR`
- AND confidence is ≥ 0.5

#### Scenario: Vague task with no recognisable signals
- GIVEN the task string `"make it work better"`
- WHEN the classifier runs
- THEN task type is `BUG_FIX` (default)
- AND confidence is 0.0
- AND `low_confidence` is true

#### Scenario: Ambiguous task with mixed signals
- GIVEN the task string `"add a fix for the broken retry logic"`
- WHEN the classifier runs
- THEN a task type is returned without crashing
- AND a confidence score between 0.0 and 1.0 is returned
- AND the result is the same on repeated calls

#### Scenario: Determinism check
- GIVEN the same task string called 5 times consecutively
- WHEN the classifier runs each time
- THEN all 5 results are identical in task type and confidence score
