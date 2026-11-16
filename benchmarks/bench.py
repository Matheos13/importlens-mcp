#!/usr/bin/env python3
"""Benchmark importlens-mcp: tokens read across four strategies on real repos.

Methodology
-----------
  all files      total tokens across every source .py file (worst-case baseline)
  grep full      files whose text contains the symbol name (word-boundary match),
                 plus the local imports of each matched file — read in full
  grep -C3       fair smart-grep baseline: defining file read in full, every
                 other mentioning file represented as call-site snippets
                 (3 lines before + 3 after each match)
  importlens     what relevant_files() actually returns: defining file + local
                 imports in full, dependent files as call-site snippets
                 (2 lines before + 3 after each match)

  test files excluded from all four counts.

Token estimate: len(file_text) // 4  (~4 chars per token, standard approximation)

Default tasks use trading-bot (~8 k tokens) and a shallow clone of httpx
(~71 k tokens). To run the defaults for httpx you need the repo cloned:

    git clone --depth=1 https://github.com/encode/httpx.git /tmp/httpx

Usage
-----
    # run built-in task list, print markdown table
    python benchmarks/bench.py

    # benchmark a single repo + symbols of your choice
    python benchmarks/bench.py /path/to/repo Symbol1 Symbol2 Symbol3
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

# Allow running from the project root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from importlens_mcp.graph import CodeIndex, call_sites  # noqa: E402


@dataclass
class Task:
    repo_label: str
    repo_path: str
    symbol: str


TASKS: list[Task] = [
    Task("trading-bot", "~/Projects/trading-bot", "RiskManager"),
    Task("trading-bot", "~/Projects/trading-bot", "TradingBot"),
    Task("trading-bot", "~/Projects/trading-bot", "BacktestEngine"),
    Task("httpx",       "/tmp/httpx",             "DigestAuth"),
    Task("httpx",       "/tmp/httpx",             "Timeout"),
    Task("httpx",       "/tmp/httpx",             "MultipartStream"),
    Task("httpx",       "/tmp/httpx",             "Client"),
    Task("httpx",       "/tmp/httpx",             "Request"),
]


def _tokens(root: str, paths: set[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += len(open(os.path.join(root, p), encoding="utf-8").read())
        except OSError:
            pass
    return total // 4


def _grep_set(root: str, idx: CodeIndex, symbol: str, src: set[str]) -> set[str]:
    """Files that mention `symbol` as a whole word, plus their local imports."""
    pat = re.compile(r"\b" + re.escape(symbol) + r"\b")
    matched: set[str] = set()
    for p in src:
        try:
            text = open(os.path.join(root, p), encoding="utf-8").read()
        except OSError:
            continue
        if pat.search(text):
            matched.add(p)
    result = set(matched)
    for p in matched:
        result.update(idx.local_imports(p))
    return result & src


def _importlens_set(idx: CodeIndex, symbol: str, src: set[str]) -> set[str]:
    """Defining files + their local imports + their dependents."""
    defs = idx.find_symbol(symbol)
    if not defs:
        return set()
    seen: set[str] = set(defs)
    for p in defs:
        seen.update(idx.local_imports(p))
        seen.update(idx.dependents(p))
    return seen & src


def _grep_c3_tokens(root: str, idx: CodeIndex, symbol: str, src: set[str]) -> int:
    """Tokens for 'grep -C3': defining file(s) in full, every other mentioning
    file as call-site snippets with 3 lines before + 3 after each match."""
    pat = re.compile(r"\b" + re.escape(symbol) + r"\b")
    defs = set(idx.find_symbol(symbol))
    total = 0
    for p in src:
        try:
            text = open(os.path.join(root, p), encoding="utf-8").read()
        except OSError:
            continue
        if not pat.search(text):
            continue
        if p in defs:
            total += len(text)
        else:
            total += len(call_sites(root, p, symbol, before=3, after=3))
    return total // 4


def _importlens_tokens(root: str, idx: CodeIndex, symbol: str, src: set[str]) -> int:
    """Tokens for what relevant_files actually returns.

    Defining files and their local imports are counted in full. Dependent files
    are counted as call-site snippets (2 lines before + 3 after each match),
    matching the default behaviour of relevant_files().
    """
    defs = set(idx.find_symbol(symbol))
    if not defs:
        return 0
    full_files: set[str] = set(defs)
    dep_files: set[str] = set()
    for p in defs:
        full_files.update(idx.local_imports(p))
        dep_files.update(idx.dependents(p))
    full_files &= src
    dep_files = (dep_files - full_files) & src

    total = 0
    for p in full_files:
        try:
            total += len(open(os.path.join(root, p), encoding="utf-8").read())
        except OSError:
            pass
    for p in dep_files:
        total += len(call_sites(root, p, symbol))
    return total // 4


def benchmark(tasks: list[Task]) -> list[tuple]:
    # (repo_label, symbol, all_tok, grep_full_tok, grep_c3_tok, il_tok, pct_vs_c3)
    cache: dict[str, tuple[CodeIndex, set[str], int]] = {}
    rows = []
    for task in tasks:
        root = os.path.expanduser(task.repo_path)
        if not os.path.isdir(root):
            print(f"[skip] {root} not found — clone it first", file=sys.stderr)
            continue

        if root not in cache:
            idx = CodeIndex(root).build()
            src = {p for p in idx.files if not p.startswith("tests/")}
            cache[root] = (idx, src, _tokens(root, src))

        idx, src, all_tok = cache[root]

        grep_files = _grep_set(root, idx, task.symbol, src)
        il_files   = _importlens_set(idx, task.symbol, src)

        if not il_files:
            print(f"[warn] symbol {task.symbol!r} not found in {root}", file=sys.stderr)
            continue

        grep_full_tok = _tokens(root, grep_files)
        grep_c3_tok   = _grep_c3_tokens(root, idx, task.symbol, src)
        il_tok        = _importlens_tokens(root, idx, task.symbol, src)
        pct           = round(100 * (1 - il_tok / grep_c3_tok)) if grep_c3_tok else 0

        rows.append((task.repo_label, task.symbol, all_tok, grep_full_tok, grep_c3_tok, il_tok, pct))
    return rows


def print_table(rows: list[tuple]) -> None:
    print("| repo | symbol | all files | grep full files | grep -C3 | importlens | % saved vs grep -C3 |")
    print("|------|--------|----------:|----------------:|---------:|-----------:|--------------------:|")
    for repo, sym, all_t, grep_full_t, grep_c3_t, il_t, pct in rows:
        print(f"| {repo} | `{sym}` | {all_t:,} | {grep_full_t:,} | {grep_c3_t:,} | {il_t:,} | {pct:+d}% |")


def _tasks_from_argv(args: list[str]) -> list[Task]:
    root = args[0]
    label = os.path.basename(root.rstrip("/"))
    return [Task(label, root, sym) for sym in args[1:]]


if __name__ == "__main__":
    tasks = _tasks_from_argv(sys.argv[1:]) if len(sys.argv) >= 3 else TASKS
    rows = benchmark(tasks)
    if rows:
        print_table(rows)
