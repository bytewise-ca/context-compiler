"""tree-sitter based parser for Python and TypeScript source files.

Extracts:
- NODE records: FILE, FUNCTION, CLASS, METHOD
- EDGE records: CALLS, IMPORTS, COVERS, DEFINED_IN
"""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

# Suppress tree-sitter deprecation warnings from older binding styles
warnings.filterwarnings("ignore", category=DeprecationWarning, module="tree_sitter")

PY_LANGUAGE = Language(tree_sitter_python.language())
TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())
JS_LANGUAGE = Language(tree_sitter_javascript.language())

TEST_FILE_PATTERNS = [
    re.compile(r"^test_.+\.py$"),
    re.compile(r"^.+_test\.py$"),
    re.compile(r"^.+\.test\.ts$"),
    re.compile(r"^.+\.spec\.ts$"),
    re.compile(r"^.+\.test\.tsx$"),
    re.compile(r"^.+\.spec\.tsx$"),
    re.compile(r"^.+\.test\.js$"),
    re.compile(r"^.+\.spec\.js$"),
    re.compile(r"^.+\.test\.jsx$"),
    re.compile(r"^.+\.spec\.jsx$"),
]


def is_test_file(path: Path) -> bool:
    return any(p.match(path.name) for p in TEST_FILE_PATTERNS)


def token_estimate(source: str) -> int:
    return math.ceil(len(source) / 4)


@dataclass
class ParsedNode:
    id: str
    file_path: str
    symbol_name: str | None
    symbol_type: str          # FILE | FUNCTION | CLASS | METHOD
    line_start: int
    line_end: int
    token_count: int
    last_modified: int        # unix timestamp (int)
    language: str             # PYTHON | TYPESCRIPT
    docstring: str = ""       # first line of docstring / JSDoc, empty if absent


@dataclass
class ParsedEdge:
    source_id: str
    target_id: str
    edge_type: str            # CALLS | IMPORTS | COVERS | DEFINED_IN


@dataclass
class ParseResult:
    nodes: list[ParsedNode] = field(default_factory=list)
    edges: list[ParsedEdge] = field(default_factory=list)
    error: str | None = None


def _clean_docstring(raw: str) -> str:
    """Strip quotes from a raw Python string literal; return first meaningful line (≤200 chars)."""
    for quote in ('"""', "'''", '"', "'"):
        if raw.startswith(quote):
            inner = raw[len(quote):]
            if inner.endswith(quote):
                inner = inner[: -len(quote)]
            break
    else:
        inner = raw
    first = next((ln.strip() for ln in inner.splitlines() if ln.strip()), "")
    return first[:200]


def _extract_python_docstring(body_node: Node, source: str) -> str:
    """Return the first meaningful line of a Python docstring from a block node."""
    if body_node is None:
        return ""
    named = body_node.named_children
    if not named:
        return ""
    first_stmt = named[0]
    if first_stmt.type != "expression_statement":
        return ""
    for sub in first_stmt.named_children:
        if sub.type == "string":
            return _clean_docstring(source[sub.start_byte : sub.end_byte])
    return ""


def _clean_jsdoc(comment: str) -> str:
    """Extract the first meaningful description line from a JSDoc block comment."""
    # Strip /** and */ then leading * from each line
    inner = comment.strip()
    if inner.startswith("/**"):
        inner = inner[3:]
    if inner.endswith("*/"):
        inner = inner[:-2]
    for line in inner.splitlines():
        line = line.strip().lstrip("*").strip()
        if line and not line.startswith("@"):  # skip @param, @returns etc.
            return line[:200]
    return ""


def _make_node_id(file_path: str, symbol_name: str | None = None) -> str:
    if symbol_name:
        return f"{file_path}::{symbol_name}"
    return file_path


def parse_file(path: Path, repo_root: Path) -> ParseResult:
    """Parse a single source file and return nodes + edges."""
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _parse_python(path, repo_root)
    elif suffix in (".ts", ".tsx"):
        return _parse_typescript(path, repo_root)
    elif suffix in (".js", ".jsx"):
        return _parse_javascript(path, repo_root)
    return ParseResult(error=f"unsupported extension: {suffix}")


def _parse_python(path: Path, repo_root: Path) -> ParseResult:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ParseResult(error=str(e))

    parser = Parser(PY_LANGUAGE)
    try:
        tree = parser.parse(source.encode())
    except Exception as e:
        return ParseResult(error=str(e))

    if tree.root_node.has_error:
        # Partial parse — still extract what we can but flag it
        pass

    rel_path = str(path.relative_to(repo_root))
    last_modified = int(path.stat().st_mtime)
    result = ParseResult()

    # FILE node
    file_node = ParsedNode(
        id=rel_path,
        file_path=rel_path,
        symbol_name=None,
        symbol_type="FILE",
        line_start=1,
        line_end=source.count("\n") + 1,
        token_count=token_estimate(source),
        last_modified=last_modified,
        language="PYTHON",
    )
    result.nodes.append(file_node)

    _walk_python(tree.root_node, source, rel_path, last_modified, result, parent_class=None)
    _extract_python_imports(tree.root_node, source, rel_path, repo_root, result)

    if is_test_file(path):
        _extract_python_covers(tree.root_node, source, rel_path, result)

    return result


def _walk_python(
    node: Node,
    source: str,
    file_path: str,
    last_modified: int,
    result: ParseResult,
    parent_class: str | None,
) -> None:
    """Recursively walk AST and extract function/class/method definitions."""
    if node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        if name_node:
            class_name = source[name_node.start_byte:name_node.end_byte]
            class_source = source[node.start_byte:node.end_byte]
            symbol_id = _make_node_id(file_path, class_name)
            body_node = node.child_by_field_name("body")
            result.nodes.append(ParsedNode(
                id=symbol_id,
                file_path=file_path,
                symbol_name=class_name,
                symbol_type="CLASS",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                token_count=token_estimate(class_source),
                last_modified=last_modified,
                language="PYTHON",
                docstring=_extract_python_docstring(body_node, source),
            ))
            result.edges.append(ParsedEdge(
                source_id=symbol_id,
                target_id=file_path,
                edge_type="DEFINED_IN",
            ))
            for child in node.children:
                _walk_python(child, source, file_path, last_modified, result, parent_class=class_name)
            return

    if node.type in ("function_definition", "async_function_definition"):
        name_node = node.child_by_field_name("name")
        if name_node:
            fn_name = source[name_node.start_byte:name_node.end_byte]
            fn_source = source[node.start_byte:node.end_byte]
            symbol_type = "METHOD" if parent_class else "FUNCTION"
            qualified = f"{parent_class}.{fn_name}" if parent_class else fn_name
            symbol_id = _make_node_id(file_path, qualified)
            body_node = node.child_by_field_name("body")
            result.nodes.append(ParsedNode(
                id=symbol_id,
                file_path=file_path,
                symbol_name=qualified,
                symbol_type=symbol_type,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                token_count=token_estimate(fn_source),
                last_modified=last_modified,
                language="PYTHON",
                docstring=_extract_python_docstring(body_node, source),
            ))
            result.edges.append(ParsedEdge(
                source_id=symbol_id,
                target_id=file_path,
                edge_type="DEFINED_IN",
            ))
            # Extract call edges within this function body
            _extract_python_calls(node, source, symbol_id, file_path, result)
        return

    for child in node.children:
        _walk_python(child, source, file_path, last_modified, result, parent_class)


def _extract_python_calls(
    fn_node: Node,
    source: str,
    caller_id: str,
    file_path: str,
    result: ParseResult,
) -> None:
    """Extract call expressions within a function body."""
    for node in _iter_nodes(fn_node):
        if node.type == "call":
            fn_child = node.child_by_field_name("function")
            if fn_child:
                callee_name = source[fn_child.start_byte:fn_child.end_byte]
                # Strip attribute access: self.foo → foo, obj.method → method
                callee_name = callee_name.split(".")[-1]
                if callee_name and callee_name.isidentifier():
                    # We store a tentative edge; resolver will match to real nodes later
                    result.edges.append(ParsedEdge(
                        source_id=caller_id,
                        target_id=f"__unresolved__::{callee_name}",
                        edge_type="CALLS",
                    ))


def _extract_python_imports(
    root: Node,
    source: str,
    file_path: str,
    repo_root: Path,
    result: ParseResult,
) -> None:
    """Extract import statements and store IMPORTS edges."""
    for node in _iter_nodes(root):
        if node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            if module_node:
                module = source[module_node.start_byte:module_node.end_byte]
                target_path = _resolve_python_module(module, file_path, repo_root)
                if target_path:
                    result.edges.append(ParsedEdge(
                        source_id=file_path,
                        target_id=target_path,
                        edge_type="IMPORTS",
                    ))
        elif node.type == "import_statement":
            for child in node.named_children:
                if child.type in ("dotted_name", "aliased_import"):
                    name_text = source[child.start_byte:child.end_byte].split(" as ")[0].strip()
                    target_path = _resolve_python_module(name_text, file_path, repo_root)
                    if target_path:
                        result.edges.append(ParsedEdge(
                            source_id=file_path,
                            target_id=target_path,
                            edge_type="IMPORTS",
                        ))


def _extract_python_covers(
    root: Node,
    source: str,
    file_path: str,
    result: ParseResult,
) -> None:
    """For test files, extract COVERS edges based on imports."""
    for node in _iter_nodes(root):
        if node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            names_node = node.child_by_field_name("name")
            if module_node and names_node:
                module = source[module_node.start_byte:module_node.end_byte]
                # Store a tentative covers edge to the module
                result.edges.append(ParsedEdge(
                    source_id=file_path,
                    target_id=f"__covers__::{module}",
                    edge_type="COVERS",
                ))


def _resolve_python_module(module: str, from_file: str, repo_root: Path) -> str | None:
    """Convert a dotted module path to a relative file path if it exists in the repo."""
    parts = module.replace(".", "/")
    candidates = [
        f"{parts}.py",
        f"{parts}/__init__.py",
    ]
    for candidate in candidates:
        if (repo_root / candidate).exists():
            return candidate
    return None


def _parse_typescript(path: Path, repo_root: Path) -> ParseResult:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ParseResult(error=str(e))

    lang = TSX_LANGUAGE if path.suffix.lower() == ".tsx" else TS_LANGUAGE
    parser = Parser(lang)
    try:
        tree = parser.parse(source.encode())
    except Exception as e:
        return ParseResult(error=str(e))

    rel_path = str(path.relative_to(repo_root))
    last_modified = int(path.stat().st_mtime)
    result = ParseResult()

    file_node = ParsedNode(
        id=rel_path,
        file_path=rel_path,
        symbol_name=None,
        symbol_type="FILE",
        line_start=1,
        line_end=source.count("\n") + 1,
        token_count=token_estimate(source),
        last_modified=last_modified,
        language="TYPESCRIPT",
    )
    result.nodes.append(file_node)

    _walk_typescript(tree.root_node, source, rel_path, last_modified, result, parent_class=None)
    _extract_typescript_imports(tree.root_node, source, rel_path, repo_root, result)

    if is_test_file(path):
        _extract_typescript_covers(tree.root_node, source, rel_path, result)

    return result


def _walk_typescript(
    node: Node,
    source: str,
    file_path: str,
    last_modified: int,
    result: ParseResult,
    parent_class: str | None,
) -> None:
    """Iterate over node.children with sibling-aware JSDoc extraction."""
    children = list(node.children)
    for i, child in enumerate(children):
        # Extract JSDoc from immediately preceding comment sibling
        jsdoc = ""
        if i > 0 and children[i - 1].type == "comment":
            prev_text = source[children[i - 1].start_byte : children[i - 1].end_byte]
            if prev_text.startswith("/**"):
                jsdoc = _clean_jsdoc(prev_text)

        if child.type in ("class_declaration", "abstract_class_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node:
                class_name = source[name_node.start_byte : name_node.end_byte]
                class_source = source[child.start_byte : child.end_byte]
                symbol_id = _make_node_id(file_path, class_name)
                result.nodes.append(ParsedNode(
                    id=symbol_id,
                    file_path=file_path,
                    symbol_name=class_name,
                    symbol_type="CLASS",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    token_count=token_estimate(class_source),
                    last_modified=last_modified,
                    language="TYPESCRIPT",
                    docstring=jsdoc,
                ))
                result.edges.append(ParsedEdge(
                    source_id=symbol_id,
                    target_id=file_path,
                    edge_type="DEFINED_IN",
                ))
                # Recurse into class body to find methods (with JSDoc per method)
                _walk_typescript(child, source, file_path, last_modified, result, parent_class=class_name)

        elif child.type in (
            "function_declaration",
            "function_expression",
            "arrow_function",
            "method_definition",
        ):
            name_node = child.child_by_field_name("name")
            if name_node:
                fn_name = source[name_node.start_byte : name_node.end_byte]
                fn_source = source[child.start_byte : child.end_byte]
                symbol_type = "METHOD" if parent_class else "FUNCTION"
                qualified = f"{parent_class}.{fn_name}" if parent_class else fn_name
                symbol_id = _make_node_id(file_path, qualified)
                result.nodes.append(ParsedNode(
                    id=symbol_id,
                    file_path=file_path,
                    symbol_name=qualified,
                    symbol_type=symbol_type,
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    token_count=token_estimate(fn_source),
                    last_modified=last_modified,
                    language="TYPESCRIPT",
                    docstring=jsdoc,
                ))
                result.edges.append(ParsedEdge(
                    source_id=symbol_id,
                    target_id=file_path,
                    edge_type="DEFINED_IN",
                ))

        else:
            # Recurse into other node types (export_statement, class_body, etc.)
            _walk_typescript(child, source, file_path, last_modified, result, parent_class)


def _extract_typescript_imports(
    root: Node,
    source: str,
    file_path: str,
    repo_root: Path,
    result: ParseResult,
) -> None:
    for node in _iter_nodes(root):
        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            if source_node:
                raw = source[source_node.start_byte:source_node.end_byte].strip("'\"")
                target = _resolve_ts_import(raw, file_path, repo_root)
                if target:
                    result.edges.append(ParsedEdge(
                        source_id=file_path,
                        target_id=target,
                        edge_type="IMPORTS",
                    ))


def _extract_typescript_covers(
    root: Node,
    source: str,
    file_path: str,
    result: ParseResult,
) -> None:
    for node in _iter_nodes(root):
        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            if source_node:
                raw = source[source_node.start_byte:source_node.end_byte].strip("'\"")
                result.edges.append(ParsedEdge(
                    source_id=file_path,
                    target_id=f"__covers__::{raw}",
                    edge_type="COVERS",
                ))


def _resolve_ts_import(raw: str, from_file: str, repo_root: Path) -> str | None:
    """Resolve a relative TypeScript import path to a repo-relative file path."""
    if not raw.startswith("."):
        return None  # external package — skip
    from_dir = (repo_root / from_file).parent
    base = (from_dir / raw).resolve()
    for ext in ("", ".ts", ".tsx", "/index.ts", "/index.tsx"):
        candidate = Path(str(base) + ext)
        if candidate.exists():
            try:
                return str(candidate.relative_to(repo_root))
            except ValueError:
                pass
    return None


def _parse_javascript(path: Path, repo_root: Path) -> ParseResult:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ParseResult(error=str(e))

    parser = Parser(JS_LANGUAGE)
    try:
        tree = parser.parse(source.encode())
    except Exception as e:
        return ParseResult(error=str(e))

    rel_path = str(path.relative_to(repo_root))
    last_modified = int(path.stat().st_mtime)
    result = ParseResult()

    file_node = ParsedNode(
        id=rel_path,
        file_path=rel_path,
        symbol_name=None,
        symbol_type="FILE",
        line_start=1,
        line_end=source.count("\n") + 1,
        token_count=token_estimate(source),
        last_modified=last_modified,
        language="JAVASCRIPT",
    )
    result.nodes.append(file_node)

    # Reuse TypeScript walker — JS and TS share the same node type names
    _walk_typescript(tree.root_node, source, rel_path, last_modified, result, parent_class=None)
    _extract_javascript_imports(tree.root_node, source, rel_path, repo_root, result)

    if is_test_file(path):
        _extract_typescript_covers(tree.root_node, source, rel_path, result)

    # Fix language field — walker sets TYPESCRIPT, override to JAVASCRIPT
    for node in result.nodes:
        if node.language == "TYPESCRIPT":
            node.language = "JAVASCRIPT"

    return result


def _extract_javascript_imports(
    root: Node,
    source: str,
    file_path: str,
    repo_root: Path,
    result: ParseResult,
) -> None:
    for node in _iter_nodes(root):
        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            if source_node:
                raw = source[source_node.start_byte:source_node.end_byte].strip("'\"")
                target = _resolve_js_import(raw, file_path, repo_root)
                if target:
                    result.edges.append(ParsedEdge(
                        source_id=file_path,
                        target_id=target,
                        edge_type="IMPORTS",
                    ))


def _resolve_js_import(raw: str, from_file: str, repo_root: Path) -> str | None:
    """Resolve a relative JS/JSX import path to a repo-relative file path."""
    if not raw.startswith("."):
        return None
    from_dir = (repo_root / from_file).parent
    base = (from_dir / raw).resolve()
    for ext in ("", ".js", ".jsx", "/index.js", "/index.jsx"):
        candidate = Path(str(base) + ext)
        if candidate.exists():
            try:
                return str(candidate.relative_to(repo_root))
            except ValueError:
                pass
    return None


def _iter_nodes(root: Node):
    """Depth-first iterator over all AST nodes."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))
