#!/usr/bin/env python3
"""Benchmark importlens-mcp: tokens read with vs without on real repos.

Methodology
-----------
  tokens_without  total tokens across all source .py files (what an agent
                  reads to explore an unfamiliar codebase on its own)
  tokens_with     tokens in only the files importlens returns for the
                  target symbol: defining file + its local imports + its
                  dependents (test files excluded from both counts)

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
    description: str


TASKS: list[Task] = [
    Task("trading-bot", "~/Projects/trading-bot", "RiskManager",    "add position-sizing floor"),
    Task("trading-bot", "~/Projects/trading-bot", "TradingBot",     "debug run-loop signal handling"),
    Task("trading-bot", "~/Projects/trading-bot", "BacktestEngine", "add Sharpe/Sortino tracking"),
    Task("httpx",       "/tmp/httpx",             "DigestAuth",     "implement OAuth2 auth flow"),
    Task("httpx",       "/tmp/httpx",             "Timeout",        "add per-request timeout override"),
    Task("httpx",       "/tmp/httpx",             "MultipartStream","fix multipart boundary encoding"),
]


def _tokens(root: str, paths: set[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += len(open(os.path.join(root, p), encoding="utf-8").read())
        except OSError:
            pass
    return total // 4


def benchmark(tasks: list[Task]) -> list[tuple]:
    cache: dict[str, tuple[CodeIndex, int]] = {}
    rows = []
    for task in tasks:
        root = os.path.expanduser(task.repo_path)
        if not os.path.isdir(root):
            print(f"[skip] {root} not found — clone it first", file=sys.stderr)
            continue

        if root not in cache:
            idx = CodeIndex(root).build()
            src = {p for p in idx.files if not p.startswith("tests/")}
            cache[root] = (idx, _tokens(root, src))

        idx, all_tok = cache[root]
        defs = idx.find_symbol(task.symbol)
        if not defs:
            print(f"[warn] symbol {task.symbol!r} not found in {root}", file=sys.stderr)
            continue

        relevant: set[str] = set(defs)
        for p in defs:
            relevant.update(idx.local_imports(p))
            relevant.update(idx.dependents(p))
        relevant = {p for p in relevant if not p.startswith("tests/")}

        with_tok = _tokens(root, relevant)
        pct = round(100 * (1 - with_tok / all_tok)) if all_tok else 0
        rows.append((task.repo_label, task.description, all_tok, with_tok, pct))
    return rows


def print_table(rows: list[tuple]) -> None:
    print("| repo | task | tokens without | tokens with | % saved |")
    print("|------|------|---------------:|------------:|--------:|")
    for repo, task, wo, wi, pct in rows:
        print(f"| {repo} | {task} | {wo:,} | {wi:,} | {pct}% |")


def _tasks_from_argv(args: list[str]) -> list[Task]:
    """Build a task list from  <repo_path> <Symbol1> [Symbol2 ...]."""
    root = args[0]
    label = os.path.basename(root.rstrip("/"))
    return [Task(label, root, sym, sym) for sym in args[1:]]


if __name__ == "__main__":
    tasks = _tasks_from_argv(sys.argv[1:]) if len(sys.argv) >= 3 else TASKS
    rows = benchmark(tasks)
    if rows:
        print_table(rows)
