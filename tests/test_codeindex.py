"""Tests for CodeIndex: modules, symbols, dependents, local imports, and CLI."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from importlens_mcp.graph import CodeIndex, call_sites, signatures


@pytest.fixture()
def fake_repo(tmp_path):
    """Minimal two-file Python project: utils.py + main.py that imports from it."""
    (tmp_path / "utils.py").write_text(textwrap.dedent("""\
        def helper():
            pass

        class Config:
            pass
    """))
    (tmp_path / "main.py").write_text(textwrap.dedent("""\
        from utils import helper

        def run():
            helper()
    """))
    return tmp_path


# ---- module discovery -------------------------------------------------------

def test_modules_found(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert "utils" in idx.module_to_path
    assert "main" in idx.module_to_path


def test_module_paths_are_relative(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert idx.module_to_path["utils"] == "utils.py"
    assert idx.module_to_path["main"] == "main.py"


# ---- symbol discovery -------------------------------------------------------

def test_symbols_found(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert "helper" in idx.symbol_to_paths
    assert "Config" in idx.symbol_to_paths
    assert "run" in idx.symbol_to_paths


def test_symbol_resolves_to_correct_file(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert idx.symbol_to_paths["helper"] == {"utils.py"}
    assert idx.symbol_to_paths["Config"] == {"utils.py"}
    assert idx.symbol_to_paths["run"] == {"main.py"}


def test_method_symbols_resolve_by_class_and_bare_name(tmp_path):
    (tmp_path / "risk.py").write_text(textwrap.dedent("""\
        class RiskManager:
            def position_size(self):
                pass

            async def refresh(self):
                pass
    """))
    (tmp_path / "portfolio.py").write_text(textwrap.dedent("""\
        class Portfolio:
            def position_size(self):
                pass
    """))
    idx = CodeIndex(str(tmp_path)).build()

    assert idx.find_symbol("RiskManager.position_size") == ["risk.py"]
    assert idx.find_symbol("Portfolio.position_size") == ["portfolio.py"]
    assert idx.find_symbol("position_size") == ["portfolio.py", "risk.py"]
    assert idx.find_symbol("RiskManager.refresh") == ["risk.py"]
    assert idx.find_symbol("refresh") == ["risk.py"]
    assert idx.find_symbol("Portfolio.refresh") == []


# ---- local imports ----------------------------------------------------------

def test_local_imports(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert "utils.py" in idx.local_imports("main.py")


def test_no_local_imports_for_leaf(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert idx.local_imports("utils.py") == []


# ---- dependents -------------------------------------------------------------

def test_dependents(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert "main.py" in idx.dependents("utils.py")


def test_no_dependents_for_root(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    assert idx.dependents("main.py") == []


# ---- stats ------------------------------------------------------------------

def test_stats(fake_repo):
    idx = CodeIndex(str(fake_repo)).build()
    s = idx.stats()
    assert s["files"] == 2
    assert s["modules"] == 2
    assert s["symbols"] >= 3  # helper, Config, run


# ---- call_sites -------------------------------------------------------------

def test_call_sites_finds_usage(fake_repo):
    snippet = call_sites(str(fake_repo), "main.py", "helper")
    assert "helper" in snippet


def test_call_sites_returns_empty_when_no_match(fake_repo):
    snippet = call_sites(str(fake_repo), "utils.py", "run")
    assert snippet == ""


def test_call_sites_includes_line_numbers(fake_repo):
    snippet = call_sites(str(fake_repo), "main.py", "helper")
    # Formatted as "   N  <content>"
    assert any(line.strip()[0].isdigit() for line in snippet.splitlines())


def test_call_sites_context_lines_around_match(fake_repo):
    # Default: 2 before + 3 after; both the import and the call site should appear
    snippet = call_sites(str(fake_repo), "main.py", "helper")
    assert "from utils import helper" in snippet
    assert "helper()" in snippet


def test_call_sites_merges_overlapping_windows(tmp_path):
    (tmp_path / "foo.py").write_text(textwrap.dedent("""\
        x = Foo()
        y = x + 1
        z = Foo()
        w = z + 1
    """))
    snippet = call_sites(str(tmp_path), "foo.py", "Foo", before=0, after=1)
    assert snippet.count("Foo") == 2


def test_call_sites_separator_between_distant_windows(tmp_path):
    lines = ["x = Foo()"] + ["pass"] * 10 + ["y = Foo()"]
    (tmp_path / "bar.py").write_text("\n".join(lines) + "\n")
    snippet = call_sites(str(tmp_path), "bar.py", "Foo", before=0, after=1)
    assert "..." in snippet


def test_call_sites_missing_file(tmp_path):
    snippet = call_sites(str(tmp_path), "nonexistent.py", "Foo")
    assert snippet == ""


# ---- signatures -------------------------------------------------------------

@pytest.fixture()
def sig_repo(tmp_path):
    (tmp_path / "lib.py").write_text(textwrap.dedent("""\
        def add(x: int, y: int) -> int:
            \"\"\"Return the sum of x and y.\"\"\"
            return x + y

        class Counter:
            \"\"\"A simple counter.\"\"\"
            count: int = 0

            def increment(self, by: int = 1) -> None:
                \"\"\"Increment the counter.\"\"\"
                self.count += by

            def reset(self) -> None:
                self.count = 0
    """))
    return tmp_path


def test_signatures_includes_def_lines(sig_repo):
    out = signatures(str(sig_repo), "lib.py")
    assert "def add(x: int, y: int) -> int:" in out
    assert "class Counter:" in out
    assert "def increment(self, by: int = 1) -> None:" in out


def test_signatures_includes_first_docstring_line(sig_repo):
    out = signatures(str(sig_repo), "lib.py")
    assert '"""Return the sum of x and y."""' in out
    assert '"""A simple counter."""' in out
    assert '"""Increment the counter."""' in out


def test_signatures_no_body(sig_repo):
    out = signatures(str(sig_repo), "lib.py")
    assert "return x + y" not in out
    assert "self.count +=" not in out


def test_signatures_includes_field_annotations(sig_repo):
    out = signatures(str(sig_repo), "lib.py")
    assert "count: int = 0" in out


def test_signatures_missing_file(sig_repo):
    assert signatures(str(sig_repo), "nonexistent.py") == ""


def test_signatures_no_docstring(tmp_path):
    (tmp_path / "plain.py").write_text("def foo(x):\n    return x\n")
    out = signatures(str(tmp_path), "plain.py")
    assert "def foo(x):" in out
    assert "return x" not in out


def test_relevant_files_local_imports_show_signatures(fake_repo):
    import importlens_mcp.server as srv
    srv._ROOT = str(fake_repo)
    srv._index = None
    # utils.py defines helper; main.py imports utils — so utils is a local import of main
    # but we look up from the defining file's perspective: Config is defined in utils.py
    # utils.py has no local imports, so test via a symbol whose def has imports
    # Use helper: defined in utils.py, main.py depends on it (dependent)
    # To test local-import signatures, make a chain: use fake_repo's main.py as the def
    result = srv.relevant_files("run")  # run is in main.py which imports utils
    # utils.py is a local import of main.py — should appear as a signature block
    assert "```python" in result
    assert "def helper" in result  # signature of utils.py


def test_relevant_files_include_full_imports(fake_repo):
    import importlens_mcp.server as srv
    srv._ROOT = str(fake_repo)
    srv._index = None
    result = srv.relevant_files("run", include_full_imports=True)
    assert "utils.py" in result
    # With full imports, no code block for the import — just the path
    # (there may still be a code block for dependents, but utils appears as a path)
    assert "- utils.py" in result


# ---- relevant_files (server) ------------------------------------------------

def test_relevant_files_default_shows_call_sites(fake_repo):
    import importlens_mcp.server as srv
    srv._ROOT = str(fake_repo)
    srv._index = None
    result = srv.relevant_files("helper")
    # Default: dependents (main.py) show inline call site snippet
    assert "```python" in result
    assert "helper" in result


def test_relevant_files_include_full_dependents_lists_path_only(fake_repo):
    import importlens_mcp.server as srv
    srv._ROOT = str(fake_repo)
    srv._index = None
    result = srv.relevant_files("helper", include_full_dependents=True)
    assert "main.py" in result
    # With full_dependents=True the code block is NOT inlined
    assert "```python" not in result


# ---- CLI: importlens-mcp map ------------------------------------------------

def test_map_cli_runs(fake_repo):
    result = subprocess.run(
        [sys.executable, "-m", "importlens_mcp", "map", str(fake_repo)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "files" in result.stdout
    assert "modules" in result.stdout


def test_map_cli_lists_modules(fake_repo):
    result = subprocess.run(
        [sys.executable, "-m", "importlens_mcp", "map", str(fake_repo)],
        capture_output=True,
        text=True,
    )
    assert "utils" in result.stdout
    assert "main" in result.stdout
