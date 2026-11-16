"""importlens-mcp MCP server.

Exposes a handful of tools that let a coding agent pull *just* the relevant
slice of a Python repo into context, instead of re-reading the whole tree.

Tools:
  - project_map      : one-glance summary of the repo
  - relevant_files   : given a symbol, return the files that matter for it
  - file_context     : a file plus its local dependencies, ready to read
"""

from __future__ import annotations

import os

from .graph import CodeIndex

# The MCP Python SDK renamed FastMCP -> MCPServer in v2. Support both so the
# server runs whether the user is on the old or new SDK.
try:
    from mcp.server.mcpserver import MCPServer as _Server  # SDK v2+
except ModuleNotFoundError:  # pragma: no cover - depends on installed version
    from mcp.server.fastmcp import FastMCP as _Server      # SDK v1

mcp = _Server("importlens-mcp")

_ROOT = os.environ.get("IMPORTLENS_ROOT", os.getcwd())
_index: CodeIndex | None = None


def _get_index(refresh: bool = False) -> CodeIndex:
    global _index
    if _index is None or refresh:
        _index = CodeIndex(_ROOT).build()
    return _index


def _read(path: str, max_lines: int = 400) -> str:
    abspath = os.path.join(_ROOT, path)
    try:
        with open(abspath, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return f"[could not read {path}]"
    if len(lines) > max_lines:
        body = "\n".join(lines[:max_lines])
        return f"{body}\n... [truncated, {len(lines) - max_lines} more lines]"
    return "\n".join(lines)


@mcp.tool()
def project_map() -> str:
    """Summarize the Python project: file count, modules, symbols, lines.

    Call this first to understand the shape of the repo before drilling in.
    """
    idx = _get_index()
    s = idx.stats()
    lines = [
        f"# Project map ({os.path.basename(_ROOT) or _ROOT})",
        "",
        f"- files: {s['files']}",
        f"- modules: {s['modules']}",
        f"- top-level symbols: {s['symbols']}",
        f"- total lines: {s['total_lines']}",
        "",
        "## Modules",
    ]
    for mod, path in sorted(idx.module_to_path.items()):
        lines.append(f"- `{mod}` -> {path}")
    return "\n".join(lines)


@mcp.tool()
def relevant_files(symbol: str, include_source: bool = False) -> str:
    """Find the files that matter for a given symbol (function or class name).

    Returns the file(s) that define it, the files that import those modules
    (dependents), and the local modules they depend on. This is the slice of
    the repo an agent needs to reason about `symbol` — usually a handful of
    files instead of the whole tree.

    Args:
        symbol: a top-level function or class name, e.g. "CodeIndex".
        include_source: if true, also inline the defining files' source.
    """
    idx = _get_index()
    defs = idx.find_symbol(symbol)
    if not defs:
        return (f"No top-level definition of `{symbol}` found. "
                "Try project_map to see available symbols.")

    out = [f"# Relevant files for `{symbol}`", ""]
    seen: set[str] = set()

    out.append("## Defined in")
    for path in defs:
        out.append(f"- {path}")
        seen.add(path)

    deps: set[str] = set()
    dependents: set[str] = set()
    for path in defs:
        deps.update(idx.local_imports(path))
        dependents.update(idx.dependents(path))

    if dependents:
        out.append("\n## Used by (dependents)")
        for path in sorted(dependents - seen):
            out.append(f"- {path}")
            seen.add(path)

    if deps:
        out.append("\n## Depends on (local imports)")
        for path in sorted(deps - set(defs)):
            out.append(f"- {path}")

    if include_source:
        out.append("\n## Source of defining files")
        for path in defs:
            out.append(f"\n### {path}\n```python\n{_read(path)}\n```")

    return "\n".join(out)


@mcp.tool()
def file_context(path: str) -> str:
    """Return a file's source plus the paths of its local dependencies.

    Use this to read one file with awareness of what it pulls in, without
    loading the whole project.

    Args:
        path: repo-relative path, e.g. "src/importlens_mcp/server.py".
    """
    idx = _get_index()
    deps = idx.local_imports(path)
    header = [f"# {path}", ""]
    if deps:
        header.append("Local dependencies:")
        header.extend(f"- {d}" for d in deps)
    else:
        header.append("Local dependencies: none")
    header.append("\n## Source\n```python")
    return "\n".join(header) + "\n" + _read(path) + "\n```"


@mcp.tool()
def reindex() -> str:
    """Rebuild the index. Call after you've changed files on disk."""
    idx = _get_index(refresh=True)
    s = idx.stats()
    return f"Reindexed: {s['files']} files, {s['symbols']} symbols."


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
