# importlens-mcp

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

## Roadmap

- [ ] call-graph edges (who calls this function), not just imports
- [ ] respect `.gitignore`
- [ ] method-level symbols, not just top-level
- [ ] `tree-sitter` backend for speed on huge repos

## Contributing

It's ~370 lines of plain Python. Read it, break it, send a PR. Good first issues are labeled.

## License

MIT
