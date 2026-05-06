#!/usr/bin/env python3
"""
Token usage measurement for context-compiler experiments.

Usage:
  # Count tokens in specific files (baseline — files Claude read manually)
  python measure_tokens.py baseline --repo /path/to/repo file1.py file2.py ...

  # Count tokens via get_context bundle (with context-compiler)
  python measure_tokens.py bundle --repo /path/to/repo --task "your task string"

  # Count tokens in the ENTIRE repo (worst-case baseline)
  python measure_tokens.py repo --repo /path/to/repo
"""

import argparse
import math
import os
import sys
from pathlib import Path

SUPPORTED = {".py", ".ts", ".tsx"}


def token_estimate(text: str) -> int:
    return math.ceil(len(text) / 4)


def count_file(path: Path) -> int:
    try:
        return token_estimate(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def cmd_baseline(repo: Path, files: list[str]) -> None:
    """Count tokens in the files you specify (the ones Claude read without the tool)."""
    total = 0
    print(f"\nBaseline — files read by Claude without context-compiler:")
    print(f"{'File':<60} Tokens")
    print("-" * 70)
    for f in files:
        p = repo / f if not Path(f).is_absolute() else Path(f)
        if not p.exists():
            print(f"  {f:<58} NOT FOUND")
            continue
        t = count_file(p)
        total += t
        print(f"  {str(p.relative_to(repo)):<58} {t:,}")
    print("-" * 70)
    print(f"  {'TOTAL':<58} {total:,}")
    print(f"\n  Files read: {len(files)}")
    print(f"  Total tokens: {total:,}")


def cmd_bundle(repo: Path, task: str, budget: int) -> None:
    """Run get_context and show the bundle token estimate."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    os.environ["CC_REPO_PATH"] = str(repo)

    from context_compiler.indexer.graph import open_database, graph_path
    import kuzu

    gp = graph_path(repo)
    if not gp.exists():
        print(f"ERROR: No index found at {gp}. Run: context-compiler index --repo {repo}")
        sys.exit(1)

    from context_compiler.retrieval.classifier import classify
    from context_compiler.retrieval.entry_nodes import find_entry_nodes
    from context_compiler.retrieval.traversal import traverse
    from context_compiler.retrieval.scorer import score_and_compile
    from context_compiler.retrieval.rationale import build_rationale_list

    db = open_database(repo)
    conn = kuzu.Connection(db)

    classification = classify(task)
    match_result = find_entry_nodes(task, conn, top_k=5)

    if not match_result.candidates:
        print(f'No entry nodes found for task: "{task}"')
        sys.exit(0)

    traversal = traverse(match_result.candidates, classification.task_type, conn)
    bundle = score_and_compile(traversal.candidates, budget, conn)
    rationales = build_rationale_list(bundle.included, match_result.keywords[0] if match_result.keywords else "")

    seen: dict[str, tuple[int, str]] = {}
    for s, r in zip(bundle.included, rationales):
        fp = s.candidate.file_path
        if fp not in seen:
            seen[fp] = (s.candidate.token_count, r)

    bundle_tokens = sum(t for t, _ in seen.values())

    # Also count total repo tokens for savings calculation
    all_py = list(repo.rglob("*.py")) + list(repo.rglob("*.ts")) + list(repo.rglob("*.tsx"))
    all_py = [f for f in all_py if ".claude-context" not in str(f)]
    repo_total = sum(count_file(f) for f in all_py)

    print(f"\nWith context-compiler — task: \"{task}\"")
    print(f"Task type: {classification.task_type.value}  (confidence: {classification.confidence:.2f})")
    print(f"\n{'File':<60} Tokens  Rationale")
    print("-" * 100)
    for fp, (tokens, rationale) in seen.items():
        print(f"  {fp:<58} {tokens:>6,}  {rationale}")
    print("-" * 100)
    print(f"  {'BUNDLE TOTAL':<58} {bundle_tokens:>6,}")
    print(f"\n  Files in bundle:  {len(seen)}")
    print(f"  Bundle tokens:    {bundle_tokens:,}")
    print(f"  Repo total:       {repo_total:,}")
    print(f"  Tokens saved:     {repo_total - bundle_tokens:,}  ({100*(repo_total-bundle_tokens)/repo_total:.1f}% reduction)")
    print(f"  Budget:           {budget:,}")


def cmd_repo(repo: Path) -> None:
    """Count total tokens in the entire repo — the worst-case baseline."""
    files = [f for f in repo.rglob("*") if f.suffix in SUPPORTED and ".claude-context" not in str(f)]
    total = 0
    print(f"\nFull repo token count: {repo}")
    print(f"{'Extension':<10} Files   Tokens")
    print("-" * 35)
    by_ext: dict[str, tuple[int, int]] = {}
    for f in files:
        t = count_file(f)
        total += t
        ext = f.suffix
        c, s = by_ext.get(ext, (0, 0))
        by_ext[ext] = (c + 1, s + t)
    for ext, (c, s) in sorted(by_ext.items()):
        print(f"  {ext:<8} {c:>5}   {s:>10,}")
    print("-" * 35)
    print(f"  {'TOTAL':<8} {len(files):>5}   {total:>10,}")


def main():
    parser = argparse.ArgumentParser(description="Measure token usage for context-compiler experiments")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_base = sub.add_parser("baseline", help="Count tokens in specific files (Claude's manual reads)")
    p_base.add_argument("--repo", required=True, type=Path)
    p_base.add_argument("files", nargs="+", help="File paths relative to repo root")

    p_bundle = sub.add_parser("bundle", help="Run get_context and show bundle tokens")
    p_bundle.add_argument("--repo", required=True, type=Path)
    p_bundle.add_argument("--task", required=True)
    p_bundle.add_argument("--budget", type=int, default=8000)

    p_repo = sub.add_parser("repo", help="Count tokens across entire repo")
    p_repo.add_argument("--repo", required=True, type=Path)

    args = parser.parse_args()

    if args.cmd == "baseline":
        cmd_baseline(args.repo.resolve(), args.files)
    elif args.cmd == "bundle":
        cmd_bundle(args.repo.resolve(), args.task, args.budget)
    elif args.cmd == "repo":
        cmd_repo(args.repo.resolve())


if __name__ == "__main__":
    main()
