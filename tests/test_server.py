"""Tests for the MCP server: every tool, its annotations, and the MCP protocol layer.

Each tool is tested two ways:
  - called directly as a Python function (fast, precise assertions)
  - called through the MCP server (`list_tools` / `call_tool`), the same path a
    real MCP client takes
"""

from __future__ import annotations

import asyncio
import os
import textwrap

import pytest

import importlens_mcp.server as srv
from importlens_mcp.server import (
    TOOL_TITLES,
    file_context,
    project_map,
    relevant_files,
    reindex,
)

ALL_TOOLS = {"project_map", "relevant_files", "file_context", "reindex"}


# ---- fixtures & helpers -----------------------------------------------------

@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """Small project with a package, a cross-module import chain, and a class.

    pkg/models.py   <- pkg/service.py   <- app.py
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "models.py").write_text(textwrap.dedent("""\
        class User:
            \"\"\"A user record.\"\"\"

            def display_name(self) -> str:
                return "user"
    """))
    (tmp_path / "pkg" / "service.py").write_text(textwrap.dedent("""\
        from pkg.models import User

        def load_user(uid: int) -> User:
            \"\"\"Load a user by id.\"\"\"
            return User()
    """))
    (tmp_path / "app.py").write_text(textwrap.dedent("""\
        from pkg.service import load_user

        def main():
            print(load_user(1).display_name())
    """))
    # Point the server at this repo and drop any index cached by other tests.
    monkeypatch.setattr(srv, "_ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "_index", None)
    return tmp_path


def _list_tools():
    return {t.name: t for t in asyncio.run(srv.mcp.list_tools())}


def _call(name: str, args: dict | None = None) -> str:
    """Call a tool through the MCP server and return its text output.

    SDK v1 returns (content, structured) or a list of content blocks;
    SDK v2 returns a CallToolResult. Normalize to the text.
    """
    result = asyncio.run(srv.mcp.call_tool(name, args or {}))
    if isinstance(result, tuple):
        content = result[0]
    elif hasattr(result, "content"):
        assert not getattr(result, "is_error", False), result
        content = result.content
    else:
        content = result
    return "".join(block.text for block in content)


def _hint(annotations, camel: str, snake: str):
    """Read an annotation field on both SDK v1 (camelCase) and v2 (snake_case)."""
    return getattr(annotations, camel, getattr(annotations, snake, None))


# ---- tool annotations -------------------------------------------------------

def test_all_tools_registered():
    assert set(_list_tools()) == ALL_TOOLS


def test_every_tool_has_a_title_entry():
    assert set(TOOL_TITLES) == ALL_TOOLS


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
def test_tool_declares_all_annotations(name):
    ann = _list_tools()[name].annotations
    assert ann is not None, f"{name} has no annotations"
    assert _hint(ann, "readOnlyHint", "read_only_hint") is True
    assert _hint(ann, "destructiveHint", "destructive_hint") is False
    assert _hint(ann, "idempotentHint", "idempotent_hint") is True
    assert _hint(ann, "openWorldHint", "open_world_hint") is False


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
def test_tool_has_human_readable_title(name):
    tool = _list_tools()[name]
    assert tool.title == TOOL_TITLES[name]
    assert tool.annotations.title == TOOL_TITLES[name]


@pytest.mark.parametrize("name", sorted(ALL_TOOLS))
def test_tool_has_description(name):
    assert _list_tools()[name].description.strip()


def _schema(tool):
    return getattr(tool, "inputSchema", None) or tool.input_schema


def test_input_schemas_mark_required_args():
    tools = _list_tools()
    assert _schema(tools["relevant_files"]).get("required") == ["symbol"]
    assert _schema(tools["file_context"]).get("required") == ["path"]
    assert not _schema(tools["project_map"]).get("required")
    assert not _schema(tools["reindex"]).get("required")


def test_relevant_files_flags_default_to_false():
    props = _schema(_list_tools()["relevant_files"])["properties"]
    for flag in ("include_source", "include_full_dependents", "include_full_imports"):
        assert props[flag]["default"] is False


# ---- project_map ------------------------------------------------------------

def test_project_map_header_uses_repo_name(repo):
    assert project_map().startswith(f"# Project map ({os.path.basename(repo)})")


def test_project_map_stats(repo):
    out = project_map()
    assert "- files: 4" in out
    assert "- modules: 4" in out
    assert "- total lines:" in out


def test_project_map_lists_modules_with_paths(repo):
    out = project_map()
    assert "- `app` -> app.py" in out
    assert f"- `pkg.models` -> {os.path.join('pkg', 'models.py')}" in out
    assert f"- `pkg.service` -> {os.path.join('pkg', 'service.py')}" in out


def test_project_map_modules_sorted(repo):
    mods = [l.split("`")[1] for l in project_map().splitlines() if l.startswith("- `")]
    assert mods == sorted(mods)
    assert mods == ["app", "pkg", "pkg.models", "pkg.service"]


def test_project_map_empty_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_ROOT", str(tmp_path))
    monkeypatch.setattr(srv, "_index", None)
    out = project_map()
    assert "- files: 0" in out
    assert "- symbols: 0" in out


def test_project_map_is_cached_between_calls(repo):
    first = project_map()
    (repo / "late.py").write_text("def late():\n    pass\n")
    # No reindex yet: the cached index is reused, so the new file is not seen.
    assert project_map() == first
    assert "late" not in project_map()


def test_project_map_via_mcp(repo):
    out = _call("project_map")
    assert out == project_map()


# ---- relevant_files ---------------------------------------------------------

def test_relevant_files_unknown_symbol(repo):
    out = relevant_files("DoesNotExist")
    assert "No definition of `DoesNotExist` found" in out
    assert "project_map" in out


def test_relevant_files_defined_in(repo):
    out = relevant_files("load_user")
    assert f"- {os.path.join('pkg', 'service.py')}" in out.split("## Used by")[0]


def test_relevant_files_dependents_and_imports(repo):
    out = relevant_files("load_user")
    assert "## Used by (dependents)" in out
    assert "### app.py" in out                       # caller snippet
    assert "load_user(1)" in out
    assert "## Depends on (local imports)" in out
    assert "class User:" in out                      # signature of pkg/models.py
    assert 'return "user"' not in out                # ...but no body


def test_relevant_files_method_symbol(repo):
    out = relevant_files("User.display_name")
    assert f"- {os.path.join('pkg', 'models.py')}" in out


def test_relevant_files_include_source(repo):
    out = relevant_files("User", include_source=True)
    assert "## Source of defining files" in out
    assert 'return "user"' in out


def test_relevant_files_without_include_source_has_no_source_section(repo):
    assert "## Source of defining files" not in relevant_files("User")


def test_relevant_files_via_mcp(repo):
    out = _call("relevant_files", {"symbol": "load_user", "include_source": True})
    assert out == relevant_files("load_user", include_source=True)


# ---- file_context -----------------------------------------------------------

def test_file_context_lists_local_dependencies(repo):
    out = file_context("app.py")
    assert out.startswith("# app.py")
    assert f"- {os.path.join('pkg', 'service.py')}" in out


def test_file_context_leaf_has_no_dependencies(repo):
    out = file_context(os.path.join("pkg", "models.py"))
    assert "Local dependencies: none" in out


def test_file_context_wraps_source_in_python_block(repo):
    out = file_context("app.py")
    assert "## Source\n```python\nfrom pkg.service import load_user" in out
    assert out.endswith("```")


def test_file_context_missing_file(repo):
    assert "[could not read nope.py]" in file_context("nope.py")


def test_file_context_blocks_path_traversal(repo, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "secret.txt"
    secret.write_text("TOP SECRET")
    rel = os.path.relpath(secret, repo)
    out = file_context(rel)
    assert "TOP SECRET" not in out
    assert "outside the project root" in out


def test_file_context_blocks_absolute_paths(repo, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "secret.txt"
    secret.write_text("TOP SECRET")
    out = file_context(str(secret))
    assert "TOP SECRET" not in out
    assert "outside the project root" in out


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_file_context_blocks_symlink_escape(repo, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "secret.txt"
    secret.write_text("TOP SECRET")
    try:
        os.symlink(secret, repo / "link.txt")
    except OSError:
        pytest.skip("symlinks not permitted here")
    out = file_context("link.txt")
    assert "TOP SECRET" not in out
    assert "outside the project root" in out


def test_file_context_allows_symlink_inside_repo(repo):
    try:
        os.symlink(repo / "app.py", repo / "alias.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    assert "from pkg.service import load_user" in file_context("alias.py")


def test_file_context_via_mcp(repo):
    assert _call("file_context", {"path": "app.py"}) == file_context("app.py")


# ---- reindex ----------------------------------------------------------------

def test_reindex_reports_counts(repo):
    s = srv._get_index().stats()
    assert reindex() == f"Reindexed: {s['files']} files, {s['symbols']} symbols."


def test_reindex_picks_up_new_files(repo):
    assert "late" not in project_map()
    (repo / "late.py").write_text("def late():\n    pass\n")
    reindex()
    assert "- `late` -> late.py" in project_map()
    assert "No definition" not in relevant_files("late")


def test_reindex_picks_up_deleted_files(repo):
    assert "- `app` -> app.py" in project_map()
    (repo / "app.py").unlink()
    reindex()
    assert "`app`" not in project_map()
    assert "No definition of `main`" in relevant_files("main")


def test_reindex_is_idempotent(repo):
    assert reindex() == reindex()


def test_reindex_does_not_touch_files_on_disk(repo):
    def snapshot():
        return {
            p: (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(repo.rglob("*")) if p.is_file()
        }
    before = snapshot()
    reindex()
    assert snapshot() == before


def test_reindex_via_mcp(repo):
    out = _call("reindex")
    assert out.startswith("Reindexed: 4 files,")


# ---- read-only guarantee across all tools -----------------------------------

def test_no_tool_writes_to_the_repo(repo):
    before = sorted(p.relative_to(repo) for p in repo.rglob("*"))
    _call("project_map")
    _call("relevant_files", {"symbol": "User", "include_source": True})
    _call("file_context", {"path": "app.py"})
    _call("reindex")
    assert sorted(p.relative_to(repo) for p in repo.rglob("*")) == before
