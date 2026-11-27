"""importlens-mcp MCP server.

Exposes a handful of tools that let a coding agent pull *just* the relevant
slice of a Python repo into context, instead of re-reading the whole tree.

Tools:
  - project_map      : one-glance summary of the repo
  - relevant_files   : given a symbol, return the files that matter for it
  - file_context     : a file plus its local dependencies, ready to read
  - reindex          : rebuild the in-memory index after files change

Every tool is read-only: importlens-mcp never writes to, deletes from, or
reaches outside the repo it indexes. That is declared to clients through MCP
tool annotations, so agents can auto-approve the calls safely.
"""

from __future__ import annotations

import os

from mcp.types import ToolAnnotations

from .graph import CodeIndex, call_sites, signatures

# The MCP Python SDK renamed FastMCP -> MCPServer in v2. Support both so the
# server runs whether the user is on the old or new SDK.
try:
    from mcp.server.mcpserver import MCPServer as _Server  # SDK v2+
except ModuleNotFoundError:  # pragma: no cover - depends on installed version
    from mcp.server.fastmcp import FastMCP as _Server      # SDK v1

mcp = _Server("importlens-mcp")


def _read_only(title: str) -> ToolAnnotations:
    """Annotations shared by every importlens-mcp tool.

    - readOnlyHint: tools only read source files; nothing on disk changes.
    - destructiveHint: nothing is ever deleted or overwritten.
    - idempotentHint: same arguments + same files on disk -> same result.
    - openWorldHint: no network, no external services; only the local repo.
    """
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


# Tool name -> human-readable title. Also used by the tests to check that
# every registered tool is annotated.
TOOL_TITLES: dict[str, str] = {
    "project_map": "Project map",
    "relevant_files": "Relevant files for a symbol",
    "file_context": "File with local dependencies",
    "reindex": "Rebuild index",
}


def _tool(name: str):
    title = TOOL_TITLES[name]
    return mcp.tool(name=name, title=title, annotations=_read_only(title))

_ROOT = os.environ.get("IMPORTLENS_ROOT", os.getcwd())
_index: CodeIndex | None = None


def _get_index(refresh: bool = False) -> CodeIndex:
    global _index
    if _index is None or refresh:
        _index = CodeIndex(_ROOT).build()
    return _index


def _inside_root(path: str) -> str | None:
    """Resolve `path` against the project root; None if it escapes the root.

    Blocks `../` traversal, absolute paths, and symlinks pointing outside the
    repo, so the read-only tools can only ever see the indexed project.
    """
    root = os.path.realpath(_ROOT)
    abspath = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([root, abspath]) != root:
        return None
    return abspath


def _read(path: str, max_lines: int | None = 400) -> str:
    abspath = _inside_root(path)
    if abspath is None:
        return f"[refusing to read {path}: outside the project root]"
    try:
        with open(abspath, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return f"[could not read {path}]"
    if max_lines is not None and len(lines) > max_lines:
        body = "\n".join(lines[:max_lines])
        return f"{body}\n... [truncated, {len(lines) - max_lines} more lines]"
    return "\n".join(lines)


@_tool("project_map")
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
        f"- symbols: {s['symbols']}",
        f"- total lines: {s['total_lines']}",
        "",
        "## Modules",
    ]
    for mod, path in sorted(idx.module_to_path.items()):
        lines.append(f"- `{mod}` -> {path}")
    return "\n".join(lines)


@_tool("relevant_files")
def relevant_files(
    symbol: str,
    include_source: bool = False,
    include_full_dependents: bool = False,
    include_full_imports: bool = False,
) -> str:
    """Find the files that matter for a given function, class, or method.

    Returns the file(s) that define it, the call sites in dependent files, and
    signatures of the local modules they depend on. This is the slice of the repo
    an agent needs to reason about `symbol` — usually a handful of files instead
    of the whole tree.

    Args:
        symbol: a function or class name, or a method name like "CodeIndex.build"
            or "build".
        include_source: if true, also inline the defining files' source.
        include_full_dependents: if true, list dependent file paths instead of
            inlining the call sites where the symbol is used.
        include_full_imports: if true, list local import paths instead of
            inlining their signatures.
    """
    idx = _get_index()
    defs = idx.find_symbol(symbol)
    if not defs:
        return (f"No definition of `{symbol}` found. "
                "Try project_map to see available symbols.")

    out = [f"# Relevant files for `{symbol}`", ""]
    seen: set[str] = set()

    out.append("## Defined in")
    for path in defs:
        out.append(f"- {path}")
        seen.add(path)

    deps: set[str] = set()
    dependents_set: set[str] = set()
    for path in defs:
        deps.update(idx.local_imports(path))
        dependents_set.update(idx.dependents(path))

    if dependents_set:
        out.append("\n## Used by (dependents)")
        if not include_full_dependents:
            out.append("> Call-site snippets only. Call `file_context(\"<path>\")` for the full file if you need more context.")
        for path in sorted(dependents_set - seen):
            if include_full_dependents:
                out.append(f"- {path}")
            else:
                snippet = call_sites(_ROOT, path, symbol)
                if snippet:
                    out.append(f"\n### {path}\n```python\n{snippet}\n```")
                else:
                    out.append(f"- {path}")
            seen.add(path)

    if deps:
        out.append("\n## Depends on (local imports)")
        if not include_full_imports:
            out.append("> Signatures only — no implementation bodies. Call `file_context(\"<path>\")` to read the full source if your change depends on how a dependency works.")
        for path in sorted(deps - set(defs)):
            if include_full_imports:
                out.append(f"- {path}")
            else:
                sig = signatures(_ROOT, path)
                if sig:
                    out.append(f"\n### {path}\n```python\n{sig}\n```")
                else:
                    out.append(f"- {path}")

    if include_source:
        out.append("\n## Source of defining files")
        for path in defs:
            out.append(f"\n### {path}\n```python\n{_read(path)}\n```")

    return "\n".join(out)


@_tool("file_context")
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
    return "\n".join(header) + "\n" + _read(path, max_lines=None) + "\n```"


@_tool("reindex")
def reindex() -> str:
    """Rebuild the index. Call after you've changed files on disk.

    Only the in-memory index is rebuilt; no files are written.
    """
    idx = _get_index(refresh=True)
    s = idx.stats()
    return f"Reindexed: {s['files']} files, {s['symbols']} symbols."


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
