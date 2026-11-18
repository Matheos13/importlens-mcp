"""Entry point: `importlens-mcp` runs the MCP server over stdio.

Subcommands (no subcommand → MCP server):
  map [path]      print the project map
  relevant <sym> [--include-callers]  print the relevant-files slice for a symbol
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    args = sys.argv[1:]

    if args and args[0] == "map":
        from .graph import CodeIndex
        root = args[1] if len(args) > 1 else "."
        idx = CodeIndex(root).build()
        s = idx.stats()
        print(f"importlens-mcp: {s['files']} files, {s['modules']} modules, "
              f"{s['symbols']} symbols, {s['total_lines']} lines")
        for mod, path in sorted(idx.module_to_path.items()):
            print(f"  {mod:<40} {path}")
        return

    if args and args[0] == "relevant":
        include_callers = "--include-callers" in args[1:]
        symbols = [arg for arg in args[1:] if arg != "--include-callers"]
        if not symbols:
            print("usage: importlens-mcp relevant <Symbol> [--include-callers]", file=sys.stderr)
            sys.exit(1)
        symbol = symbols[0]
        root = os.getcwd()
        from .graph import CodeIndex, call_sites, signatures
        idx = CodeIndex(root).build()
        defs = idx.find_symbol(symbol)
        callers = idx.callers(symbol) if include_callers else []
        if not defs and not callers:
            print(f"No top-level definition of `{symbol}` found. "
                  "Try `importlens-mcp map .` to see available symbols.",
                  file=sys.stderr)
            sys.exit(1)

        out = [f"# Relevant files for `{symbol}`", ""]
        seen: set[str] = set()

        if defs:
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
            out.append("> Call-site snippets only. Call `file_context(\"<path>\")` for the full file if you need more context.")
            for path in sorted(dependents_set - seen):
                snippet = call_sites(root, path, symbol)
                if snippet:
                    out.append(f"\n### {path}\n```python\n{snippet}\n```")
                else:
                    out.append(f"- {path}")
                seen.add(path)

        if callers:
            out.append("\n## Called by")
            out.extend(f"- {path}:{line}" for path, line in callers)

        if deps:
            out.append("\n## Depends on (local imports)")
            out.append("> Signatures only — no implementation bodies. Call `file_context(\"<path>\")` to read the full source if your change depends on how a dependency works.")
            for path in sorted(deps - set(defs)):
                sig = signatures(root, path)
                if sig:
                    out.append(f"\n### {path}\n```python\n{sig}\n```")
                else:
                    out.append(f"- {path}")

        print("\n".join(out))
        return

    # Default: run the MCP server. Index the cwd unless told otherwise.
    os.environ.setdefault("IMPORTLENS_ROOT", os.getcwd())
    from .server import run
    run()


if __name__ == "__main__":
    main()
