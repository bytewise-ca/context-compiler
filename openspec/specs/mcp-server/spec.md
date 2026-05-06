# mcp-server Specification

## Purpose
Expose the context-compiler pipeline as an MCP server using stdio transport (no HTTP endpoint, no exposed port). The server provides two tools to Claude Code and any MCP-compatible client: `get_context` for retrieving a context bundle, and `refresh` for re-indexing changed files. The server must remain responsive under all error conditions and never crash in response to bad input or a missing graph.

## Requirements

### Requirement: stdio transport only
The server SHALL communicate via stdio (stdin/stdout) using the MCP JSON-RPC 2.0 protocol. No HTTP endpoint or port SHALL be opened for local operation.

### Requirement: FastMCP framework
The server SHALL be implemented using the FastMCP library. Tool input and output types SHALL be defined as Pydantic v2 models.

### Requirement: get_context tool
The server SHALL expose a `get_context` tool with the following signature:
```python
get_context(task: str, budget: int = 8000) -> ContextBundle
```
The tool SHALL execute the full pipeline: classify → entry node match → traverse → score → rationale.

### Requirement: refresh tool
The server SHALL expose a `refresh` tool with the following signature:
```python
refresh(changed_files: list[str]) -> RefreshResult
```
In v0.1, refresh SHALL trigger a full re-index of the repository (not incremental). The tool SHALL return files processed, nodes added, nodes removed, and elapsed time.

### Requirement: ContextBundle schema
`ContextBundle` SHALL contain:
- `task_type`: enum (`BUG_FIX`, `NEW_FEATURE`, `REFACTOR`)
- `confidence`: float (0.0–1.0)
- `low_confidence`: bool (optional, present when confidence < 0.5)
- `token_estimate`: int
- `tokens_saved`: int
- `files`: list of file paths
- `rationale`: list of rationale strings (one per file, same order as `files`)
- `excluded`: list of exclusion reason strings
- `message`: str (optional, present when bundle is empty or has warnings)

### Requirement: RefreshResult schema
`RefreshResult` SHALL contain:
- `files_processed`: int
- `nodes_added`: int
- `nodes_removed`: int
- `elapsed_seconds`: float

### Requirement: GRAPH_NOT_FOUND error handling
When `get_context` or `refresh` is called and `.claude-context/graph.db` does not exist or cannot be opened, the server SHALL return a structured error bundle (not raise an exception):
```json
{
  "error": "GRAPH_NOT_FOUND",
  "message": "No index found at .claude-context/graph.db. Run: uvx context-compiler index --repo <path>",
  "files": [],
  "rationale": []
}
```

### Requirement: Empty bundle on no match
When `get_context` finds no entry node matching the task string, the server SHALL return an empty bundle with a message:
```
"No symbol matching the task string found in the graph. Use a more specific term (e.g., a function name, file name, or module name)."
```
The server SHALL NOT raise an exception or return an MCP error.

### Requirement: Server stability
The server SHALL remain running and responsive after any error condition, including: missing graph, corrupt graph, parse error during refresh, empty task string, and malformed file paths in `refresh`.

### Requirement: Startup time
The server SHALL be ready to accept tool calls within 3 seconds of being spawned by Claude Code.

### Requirement: Startable via uvx
The server SHALL be startable via:
```bash
uvx context-compiler serve --repo <path>
```

### Requirement: Registerable with Claude Code
The server SHALL be registerable in Claude Code via:
```bash
claude mcp add context-compiler uvx context-compiler serve --repo /path/to/my-project
```
After registration, `get_context` and `refresh` SHALL appear in Claude Code's `/tools` list.

### Requirement: Token budget via environment variable
The server SHALL respect the `CC_TOKEN_BUDGET` environment variable as the default token budget when the `budget` parameter is not provided to `get_context`.

### Requirement: No outbound network traffic
The server SHALL NOT transmit source code, graph data, task strings, or any other data to any external service. All operations SHALL be performed locally.

## Scenarios

#### Scenario: Successful get_context call
- GIVEN the repository has been indexed
- AND Claude Code calls `get_context(task="fix the payment retry logic")`
- WHEN the server processes the request
- THEN a `ContextBundle` is returned with `files`, `rationale`, `token_estimate`, and `tokens_saved`
- AND the response time is ≤ 500ms

#### Scenario: get_context before indexing
- GIVEN `.claude-context/graph.db` does not exist
- WHEN Claude Code calls `get_context(task="fix the retry logic")`
- THEN the server returns a structured error with `error: "GRAPH_NOT_FOUND"`
- AND the message contains the exact command to run to fix it
- AND the server continues running and accepting subsequent calls

#### Scenario: get_context with vague task string
- GIVEN the repository has been indexed
- AND Claude Code calls `get_context(task="make it work better")`
- WHEN the server processes the request
- THEN an empty bundle is returned with an actionable message
- AND no exception is raised
- AND the server remains running

#### Scenario: refresh after file edit
- GIVEN the repository has been indexed
- AND `payments/processor.py` has been modified
- WHEN Claude Code calls `refresh(changed_files=["payments/processor.py"])`
- THEN the server re-indexes and returns a `RefreshResult`
- AND subsequent `get_context` calls reflect the updated file

#### Scenario: Tools appear after registration
- GIVEN the developer runs `claude mcp add context-compiler uvx context-compiler serve --repo ./my-project`
- WHEN the developer opens a new Claude Code session
- THEN `get_context` and `refresh` appear in the `/tools` list
- AND the server starts within 3 seconds

#### Scenario: Budget override via parameter
- GIVEN Claude Code calls `get_context(task="fix the payment retry logic", budget=16000)`
- WHEN the server processes the request
- THEN the returned bundle does not exceed 16,000 tokens
- AND the `token_estimate` field reflects the actual bundle size

#### Scenario: Reproducibility across calls
- GIVEN the same repository state
- WHEN `get_context(task="fix the payment retry logic")` is called 5 times consecutively
- THEN all 5 responses are identical
- AND no randomness is introduced between calls

#### Scenario: No outbound network traffic
- GIVEN the server is running and processing requests
- WHEN network traffic is monitored
- THEN zero outbound connections are made to any external host
