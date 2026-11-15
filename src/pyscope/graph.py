"""Build an import graph of a Python project using the standard-library ast module.

No third-party parsers, no embeddings, no magic. Just walk the tree, parse each
file, and record who defines what and who imports what. That graph is enough to
answer "which files actually matter for this symbol?" without dumping the repo.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field

# Directories we never want to crawl. These are noise for an agent.
IGNORE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env",
    "node_modules", "build", "dist", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", "site-packages", ".eggs",
}


@dataclass
class FileInfo:
    """What we learned from one .py file."""
    path: str                       # repo-relative path, posix style
    module: str                     # dotted module name, e.g. pyscope.graph
    defines: set[str] = field(default_factory=set)   # top-level func/class names
    imports: set[str] = field(default_factory=set)   # dotted modules it imports
    lines: int = 0


class CodeIndex:
    """An index of a Python project: symbols, modules, and the import graph."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.files: dict[str, FileInfo] = {}          # path -> FileInfo
        self.module_to_path: dict[str, str] = {}      # dotted module -> path
        self.symbol_to_paths: dict[str, set[str]] = {}  # name -> defining paths
        self._built = False

    # ---- building -------------------------------------------------------

    def build(self) -> "CodeIndex":
        for path in self._walk():
            info = self._parse(path)
            if info is None:
                continue
            self.files[info.path] = info
            self.module_to_path[info.module] = info.path
            for name in info.defines:
                self.symbol_to_paths.setdefault(name, set()).add(info.path)
        self._built = True
        return self

    def _walk(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)

    def _rel(self, abspath: str) -> str:
        return os.path.relpath(abspath, self.root).replace(os.sep, "/")

    def _module_name(self, rel: str) -> str:
        parts = rel[:-3].split("/")           # drop .py
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        # a leading src/ is a layout convention, not part of the module path
        if parts and parts[0] == "src":
            parts = parts[1:]
        return ".".join(parts)

    def _parse(self, abspath: str) -> FileInfo | None:
        rel = self._rel(abspath)
        try:
            with open(abspath, "r", encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            return None
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            # A file we can't parse still exists; record it with no detail.
            return FileInfo(path=rel, module=self._module_name(rel),
                            lines=source.count("\n") + 1)

        info = FileInfo(path=rel, module=self._module_name(rel),
                        lines=source.count("\n") + 1)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                info.defines.add(node.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    info.imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    info.imports.add(node.module)
                elif node.level:
                    # relative import: resolve against this file's package
                    info.imports.add(self._resolve_relative(info.module, node))
        return info

    def _resolve_relative(self, module: str, node: ast.ImportFrom) -> str:
        pkg_parts = module.split(".")[:-1]           # package of current module
        if node.level > 1:
            pkg_parts = pkg_parts[: -(node.level - 1)] or []
        base = ".".join(pkg_parts)
        if node.module:
            return f"{base}.{node.module}" if base else node.module
        return base

    # ---- querying -------------------------------------------------------

    def local_imports(self, path: str) -> list[str]:
        """Imports of `path` that resolve to files inside this project."""
        info = self.files.get(path)
        if not info:
            return []
        out = []
        for mod in info.imports:
            resolved = self._resolve_module(mod)
            if resolved and resolved != path:
                out.append(resolved)
        return sorted(set(out))

    def _resolve_module(self, dotted: str) -> str | None:
        """Best-effort map a dotted import to a local file path."""
        if dotted in self.module_to_path:
            return self.module_to_path[dotted]
        # `from pkg.mod import thing` — thing may be a symbol, not a module,
        # so also try the parent module.
        parent = dotted.rsplit(".", 1)[0]
        if parent in self.module_to_path:
            return self.module_to_path[parent]
        # suffix match for odd layouts (src/, namespace packages, etc.)
        for mod, path in self.module_to_path.items():
            if mod.endswith("." + dotted) or mod == dotted:
                return path
        return None

    def dependents(self, path: str) -> list[str]:
        """Files that import the module defined by `path`."""
        info = self.files.get(path)
        if not info:
            return []
        target = info.module
        out = []
        for other_path, other in self.files.items():
            if other_path == path:
                continue
            for mod in other.imports:
                if mod == target or mod.startswith(target + ".") \
                        or target.endswith("." + mod):
                    out.append(other_path)
                    break
        return sorted(set(out))

    def find_symbol(self, name: str) -> list[str]:
        return sorted(self.symbol_to_paths.get(name, set()))

    def stats(self) -> dict:
        return {
            "files": len(self.files),
            "modules": len(self.module_to_path),
            "symbols": len(self.symbol_to_paths),
            "total_lines": sum(f.lines for f in self.files.values()),
        }
