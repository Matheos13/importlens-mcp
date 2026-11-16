"""Entry point: `importlens-mcp` runs the MCP server over stdio.

`importlens-mcp map [path]` prints the project map to the terminal — handy for a
quick look or a README gif without wiring up an agent.
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

    # Default: run the MCP server. Index the cwd unless told otherwise.
    os.environ.setdefault("IMPORTLENS_ROOT", os.getcwd())
    from .server import run
    run()


if __name__ == "__main__":
    main()
