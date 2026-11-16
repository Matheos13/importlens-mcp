# importlens-mcp

[![PyPI version](https://img.shields.io/pypi/v/importlens-mcp.svg)](https://pypi.org/project/importlens-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/Matheos13/importlens-mcp/blob/main/LICENSE)

**Stop feeding your coding agent the whole repo. Feed it the files that actually matter.**

importlens-mcp is a tiny [MCP](https://modelcontextprotocol.io) server that reads your Python project's *import graph* and hands your agent only the slice of code relevant to what it's working on — the file that defines a symbol, the files that use it, and the files it depends on. No embeddings, no vector DB, no re-reading the tree on every question.

```
npx-style one-liner for python:  uvx importlens-mcp
```

> **Why:** on a big repo, agents burn tokens (and get confused) re-reading files that have nothing to do with the task. importlens-mcp answers *"which files matter for `UserService`?"* in one call, using nothing but Python's own `ast`.

<!-- TODO: drop a 10-second demo gif here before launch -->
![demo](https://raw.githubusercontent.com/Matheos13/importlens-mcp/main/docs/demo.gif)

---

## Install

```bash
# with uv (recommended)
uvx importlens-mcp            # run straight from PyPI, no install

# or pip
pip install importlens-mcp
```

Add it to your agent. For(what I use) **Claude Code**:

```bash
claude mcp add importlens-mcp -- uvx importlens-mcp
```

For any MCP client, point it at the `importlens-mcp` command (stdio transport). It indexes the current working directory by default, or set `IMPORTLENS_ROOT`.

## What your agent gets

Four tools, all returning plain readable text:

| tool | what it does |
|------|--------------|
| `project_map` | one-glance summary: files, modules, symbols, line count |
| `relevant_files(symbol)` | the files that define a symbol + its dependents + its dependencies |
| `file_context(path)` | one file's source plus the paths of its local imports |
| `reindex` | rebuild after you change files on disk |

### Example

Ask your agent *"how does `CodeIndex` fit together?"* and under the hood it calls:

```
relevant_files("CodeIndex")
```

```
# Relevant files for `CodeIndex`

## Defined in
- src/importlens_mcp/graph.py

## Used by (dependents)
- src/importlens_mcp/__main__.py
- src/importlens_mcp/server.py

## Depends on (local imports)
- (none)
```

Three files instead of thirty. That's the whole idea.

## Quick look without an agent

```bash
importlens-mcp map .
```

```
importlens-mcp: 4 files, 4 modules, 10 symbols, 371 lines
  importlens_mcp           src/importlens_mcp/__init__.py
  importlens_mcp.graph     src/importlens_mcp/graph.py
  importlens_mcp.server    src/importlens_mcp/server.py
  ...
```

## How it works

1. Walk the repo, skipping the usual noise (`.venv`, `node_modules`, `__pycache__`, …).
2. Parse each `.py` file with the standard-library `ast` module — top-level functions/classes become *symbols*, `import` statements become *edges*.
3. Build two maps: `symbol -> files that define it` and `module -> files that import it`.
4. Answer queries by walking those maps. No network, no model calls, runs in milliseconds.

Import resolution is best-effort and handles `src/` layouts and relative imports. It won't catch dynamic imports or runtime magic — and that's a fine tradeoff for a v0.

## Benchmark

Three strategies compared on two real repos. Token estimate: `len(source) // 4`.

- **all files** — every source `.py` file (worst-case baseline)
- **grep baseline** — files whose text contains the symbol name, plus their direct local imports (what an agent actually does: grep for the name, read those files and their deps)
- **importlens** — only the files `relevant_files()` returns: defining file + local imports + dependents

| repo | symbol | all files | grep baseline | importlens | % saved vs grep |
|------|--------|----------:|--------------:|-----------:|----------------:|
| trading-bot | `RiskManager` | 8,187 | 7,884 | 5,255 | +33% |
| trading-bot | `TradingBot` | 8,187 | 7,884 | 4,794 | +39% |
| trading-bot | `BacktestEngine` | 8,187 | 7,884 | 4,625 | +41% |
| httpx | `DigestAuth` | 71,089 | 53,965 | 36,062 | +33% |
| httpx | `Timeout` | 71,089 | 61,102 | 42,838 | +30% |
| httpx | `MultipartStream` | 71,089 | 9,436 | 18,487 | −96% |
| httpx | `Client` | 71,089 | 65,077 | 58,993 | +9% |
| httpx | `Request` | 71,089 | 66,452 | 66,357 | +0% |

**Where importlens wins:** symbols with a clear dependency boundary (`DigestAuth`, `Timeout`, `RiskManager`) — it prunes noise that grep can't. **Where it doesn't:** `Client` and `Request` are imported by almost every file, so both strategies converge on most of the codebase. `MultipartStream` is the inverse case: importlens tracks *dependents* (files that import it), pulling in the large `_client.py`; grep, which only matches the literal name, is cheaper here.

Rerun with `python benchmarks/bench.py` (httpx needs `git clone --depth=1 https://github.com/encode/httpx /tmp/httpx` first).

## Roadmap

- [ ] call-graph edges (who calls this function), not just imports
- [ ] respect `.gitignore`
- [ ] method-level symbols, not just top-level
- [ ] `tree-sitter` backend for speed on huge repos

## Contributing

It's ~370 lines of plain Python. Read it, break it, send a PR. Good first issues are labeled.

## License

MIT
