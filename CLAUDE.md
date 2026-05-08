# claude-context-compiler

A local-first MCP server that indexes Python and TypeScript repositories into a dependency graph and returns the smallest correct context bundle for a given coding task — with a one-line rationale for every included file.

## What this project does

- Parses `.py`, `.ts`, `.tsx` files using tree-sitter into a KuzuDB graph
- Classifies a task string (`"fix the retry logic"`) into BUG_FIX / NEW_FEATURE / REFACTOR using keyword scoring — no LLM calls
- Finds entry nodes using BM25 over tokenised symbol names + file paths (fuzzy + optional semantic fallback)
- Traverses the graph with a strategy tuned per task type
- Scores candidates and compiles a token-budget-aware bundle
- Returns the bundle with a rationale string per file and an excluded list with reasons
- Exposes everything as an MCP server over stdio (no HTTP, no port)

## Spec-driven development

All component specs live in `openspec/specs/`. Read the relevant spec before implementing any component.

| Component | Spec | Source requirements |
|---|---|---|
| Indexer | `openspec/specs/indexer/spec.md` | FR-01, UC-01 |
| Classifier | `openspec/specs/classifier/spec.md` | FR-02, UC-08 |
| Entry node matching | `openspec/specs/entry-node-matching/spec.md` | FR-03.1 (hard problem) |
| Traversal | `openspec/specs/traversal/spec.md` | FR-03, UC-02/03/04 |
| Scorer | `openspec/specs/scorer/spec.md` | FR-04, UC-07 |
| Rationale | `openspec/specs/rationale/spec.md` | FR-05, UC-06 |
| MCP server | `openspec/specs/mcp-server/spec.md` | FR-06, UC-09/10 |


## Architecture

```
src/context_compiler/
├── indexer/
│   ├── parser.py          # tree-sitter extraction (Python + TypeScript)
│   └── graph.py           # KuzuDB schema + read/write
├── retrieval/
│   ├── classifier.py      # keyword scorer → TaskType + confidence
│   ├── entry_nodes.py     # BM25 + fuzzy + optional fastembed
│   ├── traversal.py       # BFS per task type
│   ├── scorer.py          # composite scoring + budget enforcement
│   └── rationale.py       # template-based rationale generation
├── server/
│   └── mcp_server.py      # FastMCP stdio server (get_context, refresh)
├── cli.py                 # index + explain + serve CLI entry points
└── models.py              # Pydantic v2 models (ContextBundle, RefreshResult, Node, Edge)
```

## Key design decisions

**No LLM calls in the critical path.** Classification, traversal, scoring, and rationale are all deterministic. The optional fastembed model is a static local ONNX model — not an API call.

**stdio transport, not HTTP.** The MCP server communicates via stdin/stdout. No port is opened. Claude Code spawns it as a subprocess.

**Multi-entry BFS.** Entry node matching returns top-K candidates (default K=5), not top-1. BFS runs from all K nodes and results are merged. Nodes reachable from multiple entry points score higher.

**Fastembed is optional.** Install with `pip install context-compiler[semantic]` to enable embedding-based semantic fallback for entry node matching. Base install uses BM25 + fuzzy only.

**Hard token ceiling.** The bundle never exceeds the token budget (`CC_TOKEN_BUDGET` env var or `budget` parameter). Partial file inclusion is not permitted.

**Deterministic output.** Same repo state + same task = byte-identical bundle. Score ties broken by file path (lexicographic ascending).

## Non-negotiable constraints

- Source code, graph data, and task strings MUST NOT leave the machine
- No LLM API calls in the classification, traversal, scoring, or rationale steps
- The MCP server MUST NOT crash on bad input, missing graph, or parse errors — always return a structured response
- The bundle MUST NEVER exceed the token budget

## Tech stack

| Concern | Library | Why |
|---|---|---|
| AST parsing | `tree-sitter`, `tree-sitter-python`, `tree-sitter-typescript` | Production-grade, no language server needed |
| Graph storage | `kuzu` | Embedded, no server process, Python bindings |
| BM25 retrieval | `rank-bm25` | Pure Python, zero native deps |
| Fuzzy matching | `rapidfuzz` | C extension, fast, widely available |
| Semantic fallback | `fastembed` (optional) | ONNX runtime, no PyTorch, 23MB model |
| MCP server | `fastmcp` | Anthropic-maintained, stdio support |
| CLI | `click` | Standard, well-tested |
| Models | `pydantic` v2 | Native FastMCP support, JSON Schema export |
| Packaging | `uv` / `pyproject.toml` | Supports `uvx context-compiler` invocation |

## CLI commands

```bash
# Index a repository
uvx context-compiler index --repo ./my-project

# Inspect what bundle a task would produce
uvx context-compiler explain --repo ./my-project --task "fix the retry logic"

# Start the MCP server (Claude Code spawns this automatically)
uvx context-compiler serve --repo ./my-project
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CC_TOKEN_BUDGET` | `8000` | Default token budget for get_context |
| `CC_REPO_PATH` | required | Repository path (overridden by --repo flag) |

## Performance targets

| Operation | Target |
|---|---|
| `get_context` response | ≤ 500ms p95 |
| Initial index (10k files) | ≤ 120 seconds |
| Entry node matching (BM25) | ≤ 50ms |
| Server startup | ≤ 3 seconds |

## Graph data model

```
Node
  id:            string        # "{file_path}::{symbol_name}"
  file_path:     string
  symbol_name:   string | null
  symbol_type:   FILE | FUNCTION | CLASS | METHOD
  line_start:    int
  line_end:      int
  token_count:   int           # ceil(char_count / 4)
  last_modified: timestamp
  language:      PYTHON | TYPESCRIPT

Edge
  source_id:     string → Node.id
  target_id:     string → Node.id
  edge_type:     CALLS | IMPORTS | COVERS | DEFINED_IN
```

## Before implementing any component

1. Read the relevant spec in `openspec/specs/<component>/spec.md`
2. Check all scenarios pass — they are the acceptance criteria
3. Do not add features not in the spec
4. Do not make LLM API calls from any non-server component
