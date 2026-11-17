"""Read results/runs.jsonl, compute stats, and patch the README.

Usage:  python benchmarks/agent/analyse.py [--dry-run]

The script replaces everything between
    <!-- BENCH:START -->
and
    <!-- BENCH:END -->
in the importlens-mcp README.md with the new table.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys
from collections import defaultdict

BENCH   = pathlib.Path(__file__).parent
RESULTS = BENCH / "results" / "runs.jsonl"
README  = BENCH.parent.parent / "README.md"

TASK_META = {
    "a1_commission":    ("A", "defining-file", "add commission_pct to RiskManager"),
    "a2_trailing_stop": ("A", "defining-file", "add trailing_stop_pct to RiskManager"),
    "b1_fill_ts":       ("B", "call-sites",    "timestamp every PaperBroker fill"),
    "b2_max_trades":    ("B", "call-sites",    "max_trades cap on BacktestEngine"),
    "c1_stop_low":      ("C", "dep-body",      "stop-loss on candle.low + fill at stop price"),
    "c2_warmup":        ("C", "dep-body",      "warmup period in run_sweep"),
}

TASK_ORDER = list(TASK_META)


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _pct(num: int, denom: int) -> str:
    if denom == 0:
        return "—"
    return f"{100 * num // denom}%"


def _fmt(n: float) -> str:
    if n == 0:
        return "—"
    return f"{n:,.0f}"


def _fmt_tools(n: float) -> str:
    if n == 0:
        return "—"
    return str(int(n)) if n == int(n) else f"{n:.1f}"


def load_records() -> list[dict]:
    if not RESULTS.exists():
        return []
    records = []
    with RESULTS.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def compute_stats(records: list[dict]) -> dict:
    # group: stats[task][condition] = list of records
    stats: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        if not r.get("error"):
            stats[r["task"]][r["condition"]].append(r)
    return stats


def per_task_row(task: str, stats: dict) -> dict:
    meta = TASK_META.get(task, ("?", "?", task))
    cat, kind, desc = meta

    def _s(cond: str) -> dict:
        runs = stats.get(task, {}).get(cond, [])
        passes       = sum(1 for r in runs if r.get("passed"))
        tokens       = [r.get("total_tokens", 0) for r in runs if r.get("total_tokens", 0) > 0]
        total_tokens = sum(tokens)
        tools        = [r.get("tool_calls", 0) for r in runs]
        fetched      = sum(1 for r in runs if r.get("fetched_dep_body"))
        return dict(
            n=len(runs), passes=passes,
            median_tok=_median(tokens),
            tok_per_pass=total_tokens / passes if passes else 0,
            median_tools=_median(tools),
            fetched=fetched,
        )

    w = _s("with")
    wo = _s("without")
    return dict(task=task, category=cat, kind=kind, desc=desc, with_=w, without=wo)


def build_table(records: list[dict]) -> str:
    stats = compute_stats(records)
    rows = [per_task_row(t, stats) for t in TASK_ORDER]

    n_runs = max(
        (r["with_"]["n"] + r["without"]["n"] for r in rows),
        default=0,
    )

    lines = []

    lines.append("## Agent Benchmark")
    lines.append("")
    if not records:
        lines.append("*No runs recorded yet. Execute `python benchmarks/agent/runner.py`.*")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"{len(records)} total runs "
        f"(quick: 3 tasks × with/without = 6; full: 6 tasks × with/without × 2 = 24).  "
    )
    lines.append("")

    # ── per-task table ───────────────────────────────────────────────────────
    lines.append("### Per-task results")
    lines.append("")
    lines.append(
        "| cat | task | description "
        "| with pass | w/o pass | Δ pass "
        "| with tokens | w/o tokens "
        "| with tools | w/o tools "
        "| with tok/pass | w/o tok/pass "
        "| % fetched body |"
    )
    lines.append("|-----|------|-------------|-----------|----------|--------|-------------|------------|------------|-----------|---------------|--------------|----------------|")

    sum_w_tok = sum_wo_tok = 0.0
    sum_w_pass = sum_wo_pass = sum_w_n = sum_wo_n = 0
    for r in rows:
        w, wo = r["with_"], r["without"]
        delta = (w["passes"] - wo["passes"]) if (w["n"] and wo["n"]) else None
        delta_s = (f"+{delta}" if delta and delta > 0 else str(delta)) if delta is not None else "—"
        fetch_s = "—"
        if r["category"] == "C":
            total_with = w["n"]
            fetch_s = _pct(w["fetched"], total_with) if total_with else "—"

        lines.append(
            f"| {r['category']} | `{r['task']}` | {r['desc']} "
            f"| {_pct(w['passes'], w['n'])} | {_pct(wo['passes'], wo['n'])} | {delta_s} "
            f"| {_fmt(w['median_tok'])} | {_fmt(wo['median_tok'])} "
            f"| {_fmt_tools(w['median_tools'])} | {_fmt_tools(wo['median_tools'])} "
            f"| {_fmt(w['tok_per_pass'])} | {_fmt(wo['tok_per_pass'])} "
            f"| {fetch_s} |"
        )
        sum_w_tok  += w["median_tok"]
        sum_wo_tok += wo["median_tok"]
        sum_w_pass  += w["passes"]; sum_w_n  += w["n"]
        sum_wo_pass += wo["passes"]; sum_wo_n += wo["n"]

    tok_pct = round(100 * (sum_w_tok - sum_wo_tok) / sum_wo_tok) if sum_wo_tok else 0
    tok_pct_s = f"{tok_pct:+d}%"
    lines.append(
        f"| — | **TOTAL** | — "
        f"| {sum_w_pass}/{sum_w_n} | {sum_wo_pass}/{sum_wo_n} | — "
        f"| {_fmt(sum_w_tok)} ({tok_pct_s}) | {_fmt(sum_wo_tok)} "
        f"| — | — "
        f"| — | — "
        f"| — |"
    )

    lines.append("")

    # ── category summary ─────────────────────────────────────────────────────
    lines.append("### Category summary")
    lines.append("")
    lines.append("| category | with pass% | w/o pass% | with tokens (med) | w/o tokens (med) | % fetched body (C only) |")
    lines.append("|----------|-----------|----------|-------------------|-----------------|------------------------|")

    for cat_name, cat_label in [("A", "A – defining file"), ("B", "B – call sites"), ("C", "C – dep body")]:
        cat_rows = [r for r in rows if r["category"] == cat_name]
        w_passes  = sum(r["with_"]["passes"] for r in cat_rows)
        w_n       = sum(r["with_"]["n"]      for r in cat_rows)
        wo_passes = sum(r["without"]["passes"] for r in cat_rows)
        wo_n      = sum(r["without"]["n"]      for r in cat_rows)

        w_toks  = [r["with_"]["median_tok"]  for r in cat_rows if r["with_"]["median_tok"]]
        wo_toks = [r["without"]["median_tok"] for r in cat_rows if r["without"]["median_tok"]]

        fetch_s = "—"
        if cat_name == "C":
            w_fetched = sum(r["with_"]["fetched"] for r in cat_rows)
            fetch_s = _pct(w_fetched, w_n) if w_n else "—"

        lines.append(
            f"| {cat_label} | {_pct(w_passes, w_n)} | {_pct(wo_passes, wo_n)} "
            f"| {_fmt(_median(w_toks))} | {_fmt(_median(wo_toks))} | {fetch_s} |"
        )

    lines.append("")
    lines.append(
        "> **tok/pass** = total tokens across all runs ÷ number of passing runs "
        "(lower is better; ∞ if no run passed).  "
        "**% fetched body** = fraction of *with-importlens* runs on category-C tasks "
        "where the agent called `file_context` or `Read` on the tracked dependency."
    )
    lines.append("")
    lines.append(
        "> **Note:** small repo (~1.4k source lines), 2 runs per condition — "
        "differences under ~10% are within run-to-run noise. "
        "Token counts include cached input."
    )
    lines.append("")
    return "\n".join(lines)


def patch_readme(table: str, dry_run: bool) -> None:
    text = README.read_text()
    start_tag = "<!-- BENCH:START -->"
    end_tag   = "<!-- BENCH:END -->"

    if start_tag not in text:
        # Append the section at the end of the file
        new_text = text.rstrip("\n") + "\n\n" + start_tag + "\n" + table + end_tag + "\n"
    else:
        before = text[: text.index(start_tag) + len(start_tag)]
        after  = text[text.index(end_tag):]
        new_text = before + "\n" + table + after

    if dry_run:
        print("── Dry-run: table that would be written ──")
        print(table)
        return

    README.write_text(new_text)
    print(f"README updated: {README}")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    records = load_records()
    print(f"Loaded {len(records)} records from {RESULTS}")
    table = build_table(records)
    patch_readme(table, dry_run)


if __name__ == "__main__":
    main()
