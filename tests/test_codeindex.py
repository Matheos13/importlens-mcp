"""Tests for CodeIndex: modules, symbols, dependents, local imports, and CLI."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from importlens_mcp.graph import CodeIndex


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
