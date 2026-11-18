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

def test_callers_index_executable_calls_with_sorted_locations(tmp_path):
    (tmp_path / "z.py").write_text(textwrap.dedent("""\
        from tools import helper
        helper(); helper()
        value = helper
        text = "helper()"
        # helper()
        async def run():
            await tools.helper()
            factory().helper()
        @helper()
        def decorated():
            return helper()
    """))
    (tmp_path / "a.py").write_text("package.tools.helper()\n")
    idx = CodeIndex(str(tmp_path)).build()

    assert idx.callers("helper") == [
        ("a.py", 1), ("z.py", 2), ("z.py", 7), ("z.py", 8),
        ("z.py", 9), ("z.py", 11),
    ]
    assert idx.callers("tools.helper") == [("z.py", 7)]
    assert idx.callers("package.tools.helper") == [("a.py", 1)]
    assert idx.callers("factory") == [("z.py", 8)]
    assert idx.callers("absent") == []


def test_callers_do_not_guess_names_for_dynamic_callees(tmp_path):
    (tmp_path / "calls.py").write_text(
        "handlers['helper']()\ngetattr(service, 'helper')()\n"
    )
    idx = CodeIndex(str(tmp_path)).build()
    assert idx.callers("helper") == []
    assert idx.callers("getattr") == [("calls.py", 2)]


def test_callers_rebuild_replaces_calls_in_changed_files(tmp_path):
    source = tmp_path / "calls.py"
    source.write_text("before()\n")
    idx = CodeIndex(str(tmp_path)).build()
    source.write_text("after()\n")
    idx.build()
    assert idx.callers("before") == []
    assert idx.callers("after") == [("calls.py", 1)]
    source.unlink()
    idx.build()
    assert idx.callers("after") == []


def test_callers_handle_invalid_python(tmp_path):
    (tmp_path / "broken.py").write_text("helper(\n")
    assert CodeIndex(str(tmp_path)).build().callers("helper") == []


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


# ---- notes in relevant_files output ----------------------------------------

def test_relevant_files_dependents_note(fake_repo):
    import importlens_mcp.server as srv
    srv._ROOT = str(fake_repo)
    srv._index = None
    result = srv.relevant_files("helper")
    assert "file_context" in result
    assert "snippets" in result.lower() or "snippet" in result.lower()


def test_relevant_files_imports_note(fake_repo):
    import importlens_mcp.server as srv
    srv._ROOT = str(fake_repo)
    srv._index = None
    result = srv.relevant_files("run")  # run is in main.py which imports utils
    assert "file_context" in result
    assert "signatures only" in result.lower()


def test_relevant_files_no_note_when_full_flags_set(fake_repo):
    import importlens_mcp.server as srv
    srv._ROOT = str(fake_repo)
    srv._index = None
    result_deps = srv.relevant_files("helper", include_full_dependents=True)
    result_imps = srv.relevant_files("run", include_full_imports=True)
    # notes are suppressed when full-listing flags are set
    assert "Call-site snippets only" not in result_deps
    assert "Signatures only" not in result_imps


# ---- file_context returns full source (no truncation) -----------------------

def test_file_context_returns_full_source(tmp_path):
    import importlens_mcp.server as srv
    # write a file longer than the old 400-line cap
    lines = [f"x_{i} = {i}" for i in range(450)]
    big_file = tmp_path / "big.py"
    big_file.write_text("\n".join(lines))
    srv._ROOT = str(tmp_path)
    srv._index = None
    result = srv.file_context("big.py")
    assert "x_449" in result          # last line must be present
    assert "truncated" not in result


# ---- CLI: importlens-mcp map ------------------------------------------------

def test_relevant_files_callers_are_opt_in(fake_repo, monkeypatch):
    import importlens_mcp.server as srv
    monkeypatch.setattr(srv, "_ROOT", str(fake_repo))
    monkeypatch.setattr(srv, "_index", None)

    assert "## Called by" not in srv.relevant_files("helper")
    result = srv.relevant_files("helper", include_callers=True)
    assert "## Called by\n- main.py:4" in result
    assert "## Called by" not in srv.relevant_files("Config", include_callers=True)


@pytest.mark.parametrize("args", [
    ["relevant", "helper", "--include-callers"],
    ["relevant", "--include-callers", "helper"],
])
def test_relevant_cli_exposes_callers(fake_repo, args):
    result = subprocess.run(
        [sys.executable, "-m", "importlens_mcp", *args],
        cwd=fake_repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "## Called by\n- main.py:4" in result.stdout


def test_relevant_cli_default_stays_compact(fake_repo):
    result = subprocess.run(
        [sys.executable, "-m", "importlens_mcp", "relevant", "helper"],
        cwd=fake_repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "## Called by" not in result.stdout


def test_callers_can_be_queried_without_a_local_definition(tmp_path, monkeypatch):
    import importlens_mcp.server as srv
    (tmp_path / "consumer.py").write_text("service.position_size()\n")
    monkeypatch.setattr(srv, "_ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "_index", None)
    result = srv.relevant_files("position_size", include_callers=True, include_source=True)
    assert "## Called by\n- consumer.py:1" in result
    assert "## Defined in" not in result
    assert "## Source of defining files" not in result
    cli = subprocess.run(
        [sys.executable, "-m", "importlens_mcp", "relevant", "position_size", "--include-callers"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert cli.returncode == 0, cli.stderr
    assert "## Called by\n- consumer.py:1" in cli.stdout


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
