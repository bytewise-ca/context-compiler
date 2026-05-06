# indexer Specification

## Purpose
Parse Python and TypeScript source files in a repository and build a queryable dependency graph stored in an embedded KuzuDB instance. The graph captures symbols, call relationships, import relationships, and test coverage links.

## Requirements

### Requirement: File discovery
The system SHALL scan all `.py`, `.ts`, and `.tsx` files under a given directory tree, ignoring files listed in `.gitignore`.

### Requirement: Python parsing
The system SHALL parse Python files using `tree-sitter-python` to extract function definitions, class definitions, and method definitions as graph nodes.

### Requirement: TypeScript parsing
The system SHALL parse TypeScript and TSX files using `tree-sitter-typescript` to extract function definitions, class definitions, and method definitions as graph nodes.

### Requirement: Call edge extraction
The system SHALL extract call relationships between symbols and store them as directed `CALLS` edges (caller → callee).

### Requirement: Import edge extraction
The system SHALL extract import relationships between files and store them as directed `IMPORTS` edges (importing file → imported file).

### Requirement: Test link extraction
The system SHALL detect test files matching `test_*.py`, `*_test.py`, `*.test.ts`, `*.spec.ts` and store `COVERS` edges to the symbols they reference via import or direct call.

### Requirement: Node metadata
The system SHALL store per-node metadata: file path, symbol name, symbol type (FILE, FUNCTION, CLASS, METHOD), line start, line end, token count estimate, last-modified timestamp, and language (PYTHON, TYPESCRIPT).

### Requirement: Graph storage
The system SHALL store the graph in an embedded KuzuDB instance at `.claude-context/graph.db` within the repository root. No external database server SHALL be required.

### Requirement: Gitignore update
The system SHALL append `.claude-context/` to `.gitignore` on first index if not already present.

### Requirement: Graceful skip on parse error
The system SHALL log a warning (`WARN: skipped {file} — parse error`) and continue indexing when a file cannot be parsed. It SHALL NOT silently skip files.

### Requirement: Overwrite on re-index
The system SHALL overwrite the existing graph on full re-index without requiring manual deletion.

### Requirement: Performance
The system SHALL complete initial indexing of a 10,000-file repository within 120 seconds on a standard developer laptop. It SHALL process a minimum of 100 files per second.

### Requirement: Summary output
The system SHALL print a summary on completion: file count, node count, edge count, and elapsed time.

## Scenarios

#### Scenario: Index a clean Python repository
- GIVEN a Python repository with 500 parseable `.py` files at `./my-project`
- WHEN the developer runs `uvx context-compiler index --repo ./my-project`
- THEN the system creates `.claude-context/graph.db` containing nodes for all extracted symbols
- AND `.gitignore` is updated to include `.claude-context/`
- AND a summary is printed showing file count, node count, edge count, and elapsed time
- AND the process exits with code 0

#### Scenario: Index a TypeScript repository
- GIVEN a TypeScript repository with `.ts` and `.tsx` files
- WHEN the developer runs `uvx context-compiler index --repo ./my-project`
- THEN all TypeScript symbols are extracted using `tree-sitter-typescript`
- AND import edges between files are stored in the graph
- AND the process exits with code 0

#### Scenario: File fails to parse
- GIVEN a repository containing one syntactically broken `.py` file
- WHEN indexing runs
- THEN the system logs `WARN: skipped {file} — parse error`
- AND continues indexing all remaining files
- AND the final graph excludes only the broken file

#### Scenario: Re-index an already indexed repository
- GIVEN `.claude-context/graph.db` already exists
- WHEN the developer runs the index command again
- THEN the system overwrites the existing graph with a fresh index
- AND no manual deletion is required

#### Scenario: Empty repository
- GIVEN a repository with no `.py`, `.ts`, or `.tsx` files
- WHEN indexing runs
- THEN the system creates an empty graph
- AND warns that `get_context` will return empty bundles

#### Scenario: Performance on large repository
- GIVEN a repository with 10,000 parseable files
- WHEN indexing runs
- THEN the process completes within 120 seconds
