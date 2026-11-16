"""Tests for CodeIndex: modules, symbols, dependents, local imports, and CLI."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from importlens_mcp.graph import CodeIndex, call_sites


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
