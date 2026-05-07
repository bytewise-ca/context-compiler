# context-compiler

A local-first MCP server that indexes your Python and TypeScript codebase into a dependency graph and returns the **smallest correct context bundle** for any coding task — with a one-line rationale for every included file.

No cloud. No LLM API calls. No data leaves your machine.

---

## The problem

When you ask Claude to fix a bug or add a feature, it reads files by guessing which ones are relevant. It over-reads (wastes tokens) or misses the file that actually matters. The bigger the codebase, the worse this gets.

## How it works

```
Your task: "fix the payment retry logic"
         ↓
  Classify → BUG_FIX
         ↓
  Find entry nodes → payment_processor.py (BM25 + docstring matching)
         ↓
  Traverse graph → payment_processor.py + retry_handler.py + test_processor.py
         ↓
  Score + budget → 870 tokens (within 8000 limit)
         ↓
  Return bundle with rationale per file
```

Everything — classification, traversal, scoring, rationale — is deterministic. Same repo + same task = same bundle, every time.

---

## Getting started

```bash
pip install claude-context-compiler
```

```bash
cd /your/project
context-compiler init
```

That's it. `init` does three things in one step:
1. Indexes your codebase into a local dependency graph
2. Registers the MCP server with Claude Code
3. Adds context-retrieval instructions to `CLAUDE.md`

Then open Claude Code in your project — it will call `get_context` automatically before reading files.

Requires Python 3.11+.

### Other commands

```bash
# Re-index after large changes
context-compiler index

# Preview what context a task would produce (no Claude needed)
context-compiler explain --task "<Prompt>"
```

All commands default to the current directory. Pass `--repo <path>` to target a different path.

### Optional: semantic fallback
 [ This is not in prod yet; work in progress ] 
For better matching when task terms don't appear in symbol names (e.g. "fix login flow" → finds `authenticate_user`):

```bash
pip install "claude-context-compiler[semantic]"
```

Downloads a 23MB ONNX model once, no PyTorch required.

---

## MCP tools

### `get_context(task, budget=8000)`

Returns the minimal file bundle for a coding task.

```json
{
  "files": ["payments/processor.py", "payments/retry_handler.py", "tests/test_processor.py"],
  "rationale": [
    "Included PaymentProcessor as primary task location (matched 'payment')",
    "Included RetryHandler because it is called by PaymentProcessor (depth 1)",
    "Included test_processor.py — test coverage for PaymentProcessor"
  ],
  "confidence": 1.0
}
```

### `refresh(changed_files)`

Re-indexes the repository after file changes.

---

## What makes it different

**Task-type-aware traversal.** A bug fix traverses inbound callers and test coverage at depth 2. A new feature traverses imports and sibling modules. A refactor traverses everything at depth 3. No other tool adjusts retrieval strategy based on what you're actually trying to do.

**Rationale per file.** Every included file has a one-line explanation of why it's there. You can see what Claude will read before it reads it.

**Hard token budget.** The bundle never exceeds the limit. Partial file inclusion is not permitted.

**Local-first.** Embedded KuzuDB graph, no server, no port, no auth. Works offline.

---

## Supported languages

| Language | Parsing | Docstrings |
|---|---|---|
| Python | tree-sitter-python | ✓ (first line of docstring) |
| TypeScript / TSX | tree-sitter-typescript | ✓ (JSDoc `/** */`) |

---


## Tech stack

[tree-sitter](https://tree-sitter.github.io/) · [KuzuDB](https://kuzudb.com/) · [BM25 (rank-bm25)](https://github.com/dorianbrown/rank_bm25) · [rapidfuzz](https://github.com/maxbachmann/RapidFuzz) · [FastMCP](https://github.com/jlowin/fastmcp) · [fastembed](https://github.com/qdrant/fastembed) (optional)

---

## License

Apache 2.0
