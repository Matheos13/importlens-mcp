"""Six benchmark tasks on the trading-bot repo.

Categories
----------
A  defining-file  — the fix lives entirely in one file; no call sites or dep bodies needed
B  call-sites     — agent must find all callers to implement correctly
C  dep-body       — agent must read a dependency's full implementation, not just its signature

dep_files
---------
For category C tasks, dep_files lists the repo-relative paths whose full source the agent
should fetch (via file_context or Read).  The runner records whether each path was
actually opened, letting us compare fetching rates between conditions.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Task:
    name: str
    category: str           # "A" | "B" | "C"
    prompt: str             # may contain {worktree} placeholder
    test_files: list[str]   # filenames in hidden_tests/ to copy + run
    dep_files: list[str]    # repo-relative dep paths to monitor (empty for A/B)


TASKS: list[Task] = [

    # ── A1: commission adjustment in RiskManager ────────────────────────────
    Task(
        name="a1_commission",
        category="A",
        prompt="""\
You are working in the trading-bot project at {worktree}.

Add a `commission_pct: float = 0.001` field to `RiskManager`.
Adjust `position_size` so that round-trip commission is deducted from the
risk budget using this formula:

    size = (equity * risk_per_trade) / (price * stop_loss_pct + 2 * price * commission_pct)

Existing code that constructs `RiskManager()` with no arguments must still work.
Make the minimum necessary change.
""",
        test_files=["test_a1_commission.py"],
        dep_files=[],
    ),

    # ── A2: trailing stop on RiskManager ────────────────────────────────────
    Task(
        name="a2_trailing_stop",
        category="A",
        prompt="""\
You are working in the trading-bot project at {worktree}.

Add a `trailing_stop_pct: float | None = None` field to `RiskManager`.
Add a method `trail_stop_price(peak_price: float) -> float` that returns
`peak_price * (1 - trailing_stop_pct)`.
If `trailing_stop_pct` is None the method must raise
`ValueError("trailing stop not configured")`.
Existing code must still work without modification.
""",
        test_files=["test_a2_trailing_stop.py"],
        dep_files=[],
    ),

    # ── B1: timestamps on PaperBroker fills ─────────────────────────────────
    Task(
        name="b1_fill_ts",
        category="B",
        prompt="""\
You are working in the trading-bot project at {worktree}.

Add a `ts: int = 0` field to the `Fill` dataclass in `PaperBroker`.
Add a `ts: int = 0` keyword-only argument to both `PaperBroker.buy` and
`PaperBroker.sell`; store it in the `Fill`.

Then update `BacktestEngine` so that every fill it creates passes
`ts=candle.ts`.  There are multiple buy and sell calls inside `BacktestEngine.run`
— find them all.  `TradingBot` may keep `ts=0` for now.
""",
        test_files=["test_b1_fill_ts.py"],
        dep_files=[],
    ),

    # ── B2: max_trades cap on BacktestEngine ─────────────────────────────────
    Task(
        name="b2_max_trades",
        category="B",
        prompt="""\
You are working in the trading-bot project at {worktree}.

Add a `max_trades: int | None = None` parameter to `BacktestEngine.__init__`.
When set, the engine stops opening new positions after `max_trades` completed
round-trip trades (a completed trade = one sell that closes the position).
The engine may still close any position that is currently open when the limit
is reached.

All existing call sites of `BacktestEngine(...)` must continue to work without
modification — verify that sweep.py and the CLI entry point don't need changes.
""",
        test_files=["test_b2_max_trades.py"],
        dep_files=[],
    ),

    # ── C1: stop-loss on candle.low, fill at stop price ──────────────────────
    Task(
        name="c1_stop_low",
        category="C",
        prompt="""\
You are working in the trading-bot project at {worktree}.

Stop-losses in the backtest engine are checked against the candle's closing
price.  This is too lenient — if the candle's low dipped below the stop level
the stop would have fired intra-candle.

Fix:
1. Check the stop-loss condition against `candle.low` instead of the closing price.
2. When the stop fires, execute the sell at the exact stop-loss price level
   (not the closing price).  RiskManager already has the formula for the stop price.

Make the minimum necessary change.
""",
        test_files=["test_c1_stop_low.py"],
        dep_files=["src/tradingbot/risk/manager.py"],
    ),

    # ── C2: warmup period in run_sweep ───────────────────────────────────────
    Task(
        name="c2_warmup",
        category="C",
        prompt="""\
You are working in the trading-bot project at {worktree}.

Add a `warmup: int = 0` parameter to `run_sweep`.  The first `warmup` candles
should build up each strategy's internal indicator state (the strategy receives
those candles via `on_candle`) but no positions are opened and equity tracking
starts fresh only after the warmup period.

If `warmup >= len(candles)` raise `ValueError("warmup must be less than candle count")`.

Strategy state accumulated during warmup must be preserved — do not reset the
strategy between the warmup and the main run.  Read BacktestEngine to understand
how it manages strategy and broker state so you implement this correctly.
""",
        test_files=["test_c2_warmup.py"],
        dep_files=["src/tradingbot/backtest/engine.py"],
    ),
]
