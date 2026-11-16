#!/usr/bin/env python3
"""Benchmark importlens-mcp: tokens read across three strategies on real repos.

Methodology
-----------
  all files     total tokens across every source .py file (worst-case baseline)
  grep          files whose text contains the symbol name (word-boundary match),
                plus the local imports of each matched file — this is what an
                agent actually does: grep for the name, then read those files
                and their direct dependencies
  importlens    files from relevant_files(): defining file + local imports +
                dependents (test files excluded from all three counts)

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

from importlens_mcp.graph import CodeIndex  # noqa: E402


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


def benchmark(tasks: list[Task]) -> list[tuple]:
    # (repo_label, symbol, all_tok, grep_tok, il_tok, pct_vs_grep)
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

        grep_tok = _tokens(root, grep_files)
        il_tok   = _tokens(root, il_files)
        pct      = round(100 * (1 - il_tok / grep_tok)) if grep_tok else 0

        rows.append((task.repo_label, task.symbol, all_tok, grep_tok, il_tok, pct))
    return rows


def print_table(rows: list[tuple]) -> None:
    print("| repo | symbol | all files | grep baseline | importlens | % saved vs grep |")
    print("|------|--------|----------:|--------------:|-----------:|----------------:|")
    for repo, sym, all_t, grep_t, il_t, pct in rows:
        print(f"| {repo} | `{sym}` | {all_t:,} | {grep_t:,} | {il_t:,} | {pct:+d}% |")


def _tasks_from_argv(args: list[str]) -> list[Task]:
    root = args[0]
    label = os.path.basename(root.rstrip("/"))
    return [Task(label, root, sym) for sym in args[1:]]


if __name__ == "__main__":
    tasks = _tasks_from_argv(sys.argv[1:]) if len(sys.argv) >= 3 else TASKS
    rows = benchmark(tasks)
    if rows:
        print_table(rows)
