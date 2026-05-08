# context-compiler

An MCP server that indexes your codebase into a dependency graph and returns the **smallest correct context** for any coding task: exact line ranges per symbol, with a rationale for each one.

**Runs entirely on your machine.** No cloud, no LLM API calls, no embeddings server, no internet connection required. Your source code and task descriptions never leave your laptop. The base install is lightweight: pure Python, no GPU, no heavy ML framework.

---

## Getting started

```bash
pip install claude-context-compiler
cd /your/project
context-compiler init
```

`init` indexes your codebase, registers the MCP server with Claude Code, and adds instructions to `CLAUDE.md`. Open Claude Code and it will call `get_context` automatically before reading files.

Requires Python 3.11+.

### Multi-repo projects

```bash
context-compiler init --dependencies ../sbc-pay,../sbc-web
```

Each repo is indexed separately. `get_context` queries all graphs and returns the best-matching symbols across all repos. The dependency list is saved and picked up automatically on next start.

### Other commands

```bash
context-compiler index                        # re-index after large changes
context-compiler explain --task "<prompt>"    # preview what context a task returns
```

### Optional: semantic search

```bash
pip install "claude-context-compiler[semantic]"
```

Enables embedding-based fallback for cases where task terms don't appear in symbol names (e.g. "fix login flow" finds `authenticate_user`). Downloads a 23MB ONNX model once, no PyTorch required.

---

## How it works

Every step runs locally with no external calls:

1. Classify the task: `BUG_FIX`, `NEW_FEATURE`, or `REFACTOR` (keyword scoring, no LLM)
2. Find entry nodes via BM25 over symbol names, file paths, and docstrings
3. Traverse the dependency graph with a strategy tuned per task type
4. Score candidates, enforce a token budget, return slices with rationale

The graph is stored in an embedded KuzuDB database in your project folder. No server process, no port, no auth.

Same repo + same task = same output, every time.

---

## Output

```json
{
  "slices": [
    {
      "file_path": "/abs/path/payments/processor.py",
      "line_start": 6,
      "line_end": 24,
      "rationale": "Included PaymentProcessor as primary task location (matched 'payment')"
    },
    {
      "file_path": "/abs/path/payments/retry_handler.py",
      "line_start": 12,
      "line_end": 38,
      "rationale": "Included RetryHandler because it is called by PaymentProcessor (depth 1)"
    }
  ]
}
```

Each slice points to the specific function or class that's relevant. A 500-line file with one relevant function costs ~40 tokens, not 500.

---

## Supported languages

| Language | Parsing |
|---|---|
| Python | tree-sitter-python |
| TypeScript / TSX | tree-sitter-typescript |
| JavaScript / JSX | tree-sitter-javascript |

---

## License

Apache 2.0
