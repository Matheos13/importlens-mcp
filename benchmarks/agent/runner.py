"""Benchmark runner.

Usage
-----
    python benchmarks/agent/runner.py              # quick mode: 1 task/category, 1 run each (6 runs)
    python benchmarks/agent/runner.py --full       # all 24 runs (6 tasks × 2 conditions × 2 runs)
    python benchmarks/agent/runner.py a1_commission  # one specific task only
    python benchmarks/agent/runner.py --dry-run    # print plan, skip execution

Each run:
  1. fresh git worktree of ~/Projects/trading-bot
  2. ~/.claude/CLAUDE.md moved away for the duration
  3. claude -p <prompt> --output-format stream-json --verbose
       with:    --strict-mcp-config --mcp-config <importlens-config>
       without: --strict-mcp-config --mcp-config <empty-config>
  4. copy hidden tests into worktree, run pytest (PYTHONPATH=src)
  5. append RunRecord to results/runs.jsonl
  6. remove worktree, restore CLAUDE.md
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field

# ── paths ────────────────────────────────────────────────────────────────────

BENCH      = pathlib.Path(__file__).parent
REPO       = pathlib.Path.home() / "Projects" / "trading-bot"
HIDDEN     = BENCH / "hidden_tests"
RESULTS    = BENCH / "results" / "runs.jsonl"
CLAUDE_MD  = pathlib.Path.home() / ".claude" / "CLAUDE.md"
_MD_BACKUP = pathlib.Path(tempfile.gettempdir()) / "CLAUDE.md.bench.backup"

# ── MCP configs ──────────────────────────────────────────────────────────────

_MCP_WITH_TEMPLATE = {
    "mcpServers": {
        "importlens": {
            "command": "uvx",
            "args": ["importlens-mcp@latest"],
            "env": {"IMPORTLENS_ROOT": "__WORKTREE__"},
        }
    }
}

_MCP_WITHOUT = {"mcpServers": {}}


# ── result record ─────────────────────────────────────────────────────────────

@dataclass
class RunRecord:
    task:                str
    category:            str
    condition:           str    # "with" | "without"
    run_index:           int
    input_tokens:        int   = 0
    output_tokens:       int   = 0
    cache_read_tokens:   int   = 0
    cache_creation_tokens: int = 0
    total_tokens:        int   = 0
    tool_calls:          int   = 0
    fetched_dep_body:    bool  = False
    passed:              bool  = False
    pytest_output:       str   = ""
    claude_stderr:       str   = ""
    error:               str   = ""
    duration_s:          float = 0.0


# ── CLAUDE.md management ──────────────────────────────────────────────────────

def _hide_claude_md() -> None:
    if CLAUDE_MD.exists():
        shutil.move(str(CLAUDE_MD), str(_MD_BACKUP))


def _restore_claude_md() -> None:
    if _MD_BACKUP.exists():
        shutil.move(str(_MD_BACKUP), str(CLAUDE_MD))


# ── MCP config helpers ────────────────────────────────────────────────────────

def _write_mcp_config(condition: str, worktree: str, out: pathlib.Path) -> None:
    if condition == "with":
        cfg = json.loads(json.dumps(_MCP_WITH_TEMPLATE))
        cfg["mcpServers"]["importlens"]["env"]["IMPORTLENS_ROOT"] = worktree
    else:
        cfg = _MCP_WITHOUT
    out.write_text(json.dumps(cfg))


# ── stream-json parser ────────────────────────────────────────────────────────

def _is_dep_fetch(tool_name: str, tool_input: dict, dep_files: list[str]) -> bool:
    """Return True if the tool call opens one of the tracked dependency files."""
    if tool_name not in ("file_context", "Read", "read_file", "cat"):
        return False
    path = str(
        tool_input.get("path")
        or tool_input.get("file_path")
        or tool_input.get("filename")
        or ""
    )
    return any(dep in path for dep in dep_files)


def _parse_stream(lines: list[str], dep_files: list[str]) -> dict:
    input_tok = output_tok = cache_read = cache_creation = 0
    tool_calls = 0
    fetched = False

    def _check_tool(name: str, inp: dict) -> None:
        nonlocal tool_calls, fetched
        tool_calls += 1
        if dep_files and _is_dep_fetch(name, inp, dep_files):
            fetched = True

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        t = obj.get("type", "")

        # ── final result event (most reliable token source) ──────────────
        if t == "result":
            u = obj.get("usage", {})
            input_tok      = u.get("input_tokens",              input_tok)
            output_tok     = u.get("output_tokens",             output_tok)
            cache_read     = u.get("cache_read_input_tokens",   cache_read)
            cache_creation = u.get("cache_creation_input_tokens", cache_creation)

        # ── top-level tool_use event (emitted by --verbose) ──────────────
        if t == "tool_use":
            name = obj.get("tool_name") or obj.get("name", "")
            inp  = obj.get("tool_input") or obj.get("input", {}) or {}
            _check_tool(name, inp)

        # ── tool_use blocks inside assistant message content ─────────────
        msg = obj.get("message") or {}
        if isinstance(msg, dict):
            u = msg.get("usage", {})
            if u:
                input_tok  = max(input_tok,  u.get("input_tokens",  0))
                output_tok = max(output_tok, u.get("output_tokens", 0))
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    _check_tool(block.get("name", ""), block.get("input", {}) or {})

    total = input_tok + output_tok + cache_read + cache_creation
    return dict(
        input_tokens=input_tok,
        output_tokens=output_tok,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        total_tokens=total,
        tool_calls=tool_calls,
        fetched_dep_body=fetched,
    )


# ── single run ────────────────────────────────────────────────────────────────

def run_one(task, condition: str, run_index: int, dry_run: bool = False) -> RunRecord:
    rec = RunRecord(task.name, task.category, condition, run_index)
    label = f"{task.name}/{condition}/run{run_index}"
    print(f"\n{'─'*60}\n▶  {label}", flush=True)

    if dry_run:
        print("   [dry-run — skipped]")
        return rec

    wt_name = f"bench-{task.name}-{condition}-{run_index}"
    wt_path = (pathlib.Path(tempfile.gettempdir()) / wt_name).resolve()
    wt_src  = str(wt_path / "src")

    # ── create fresh worktree ──────────────────────────────────────────────
    # Remove leftover worktree from a previous failed run if present
    subprocess.run(
        ["git", "-C", str(REPO), "worktree", "remove", str(wt_path), "--force"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(REPO), "worktree", "add", str(wt_path), "HEAD"],
        capture_output=True, check=True,
    )

    # ── env: PYTHONPATH must point at the worktree so the editable install
    #    of trading-bot from ~/Projects/trading-bot is shadowed ──────────
    wt_env = os.environ.copy()
    wt_env["PYTHONPATH"] = wt_src

    # ── isolation check: abort if tradingbot still loads from the wrong place
    iso = subprocess.run(
        [sys.executable, "-c",
         f"import tradingbot; "
         f"assert {repr(wt_src)} in tradingbot.__file__, "
         f"f'loads from {{tradingbot.__file__}}'"],
        env=wt_env, capture_output=True, text=True,
    )
    if iso.returncode != 0:
        detail = (iso.stderr or iso.stdout).strip()
        rec.error = f"isolation_check_failed: {detail}"
        print(f"   ✗ {rec.error}")
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "remove", str(wt_path), "--force"],
            capture_output=True,
        )
        return rec

    mcp_cfg_path = wt_path / "_bench_mcp.json"
    _write_mcp_config(condition, str(wt_path), mcp_cfg_path)

    # Allow Edit/Write within the worktree without interactive approval prompts.
    wt_claude = wt_path / ".claude"
    wt_claude.mkdir(exist_ok=True)
    (wt_claude / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Edit(**)", "Write(**)"]},
    }))

    prompt = task.prompt.format(worktree=str(wt_path))

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--mcp-config", str(mcp_cfg_path),
        "--add-dir", str(wt_path),
    ]

    _hide_claude_md()
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=360,
            cwd=str(wt_path),
            env=wt_env,
        )
        stdout_lines = proc.stdout.splitlines()
        rec.claude_stderr = proc.stderr[-2000:] if proc.stderr else ""
    except subprocess.TimeoutExpired:
        rec.error = "claude_timeout"
        print(f"   ✗ timeout after 360 s")
        return rec
    except Exception as exc:
        rec.error = str(exc)
        print(f"   ✗ {exc}")
        return rec
    finally:
        _restore_claude_md()
        rec.duration_s = round(time.monotonic() - t0, 1)

    # ── parse stream ──────────────────────────────────────────────────────
    parsed = _parse_stream(stdout_lines, task.dep_files)
    rec.input_tokens          = parsed["input_tokens"]
    rec.output_tokens         = parsed["output_tokens"]
    rec.cache_read_tokens     = parsed["cache_read_tokens"]
    rec.cache_creation_tokens = parsed["cache_creation_tokens"]
    rec.total_tokens          = parsed["total_tokens"]
    rec.tool_calls            = parsed["tool_calls"]
    rec.fetched_dep_body      = parsed["fetched_dep_body"]
    print(f"   tokens={rec.total_tokens}  tools={rec.tool_calls}  "
          f"fetched_dep={rec.fetched_dep_body}  dur={rec.duration_s}s")

    # ── copy hidden tests + run pytest ────────────────────────────────────
    for fname in task.test_files:
        shutil.copy(HIDDEN / fname, wt_path / fname)

    env = wt_env  # same PYTHONPATH as the claude run
    test_cmd = [
        sys.executable, "-m", "pytest",
        *[str(wt_path / f) for f in task.test_files],
        "-q", "--tb=short", "--no-header",
    ]
    try:
        pr = subprocess.run(
            test_cmd, capture_output=True, text=True,
            cwd=str(wt_path), env=env, timeout=120,
        )
        rec.passed = pr.returncode == 0
        rec.pytest_output = (pr.stdout + pr.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        rec.pytest_output = "pytest timeout"
    except Exception as exc:
        rec.pytest_output = str(exc)

    status = "✓ PASS" if rec.passed else "✗ FAIL"
    print(f"   pytest: {status}")
    if not rec.passed:
        # Print last few lines of pytest output for quick diagnosis
        tail = "\n".join(rec.pytest_output.splitlines()[-12:])
        print(f"   {tail}")

    # ── cleanup worktree ──────────────────────────────────────────────────
    subprocess.run(
        ["git", "-C", str(REPO), "worktree", "remove", str(wt_path), "--force"],
        capture_output=True,
    )

    return rec


# ── persistence ───────────────────────────────────────────────────────────────

def _append_record(rec: RunRecord) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a") as fh:
        fh.write(json.dumps(asdict(rec)) + "\n")


def _already_run(task_name: str, condition: str, run_index: int) -> bool:
    if not RESULTS.exists():
        return False
    with RESULTS.open() as fh:
        for line in fh:
            try:
                r = json.loads(line)
                if (r["task"] == task_name
                        and r["condition"] == condition
                        and r["run_index"] == run_index
                        and not r.get("error")):
                    return True
            except (json.JSONDecodeError, KeyError):
                continue
    return False


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    from tasks import TASKS  # local import so runner works from its own dir

    args = sys.argv[1:]
    dry_run     = "--dry-run" in args
    full_mode   = "--full" in args
    task_filter = {a for a in args if not a.startswith("--")}

    tasks = [t for t in TASKS if not task_filter or t.name in task_filter]
    if not tasks:
        sys.exit(f"No tasks matched filter {task_filter!r}")

    # Quick mode (default): first task from each category, 1 run each → 6 runs.
    # Full mode (--full): all tasks, 2 runs each → 24 runs.
    if not task_filter and not full_mode:
        seen_cats: set[str] = set()
        quick_tasks = []
        for t in tasks:
            if t.category not in seen_cats:
                seen_cats.add(t.category)
                quick_tasks.append(t)
        tasks = quick_tasks
        run_indices = (1,)
    else:
        run_indices = (1, 2)

    plan = [(t, cond, idx)
            for t in tasks
            for cond in ("with", "without")
            for idx in run_indices]

    mode_label = "quick" if (not task_filter and not full_mode) else "full" if full_mode else "filtered"
    print(f"Plan: {len(plan)} runs  |  mode={mode_label}  |  dry_run={dry_run}")

    for task, condition, run_index in plan:
        if not dry_run and _already_run(task.name, condition, run_index):
            print(f"\n⏭  {task.name}/{condition}/run{run_index} — already in results, skipping")
            continue
        rec = run_one(task, condition, run_index, dry_run=dry_run)
        if not dry_run:
            _append_record(rec)

    if not dry_run:
        print(f"\n\nAll done.  Results → {RESULTS}")
        print("Run  python benchmarks/agent/analyse.py  to update the README table.")


if __name__ == "__main__":
    os.chdir(BENCH)
    main()
