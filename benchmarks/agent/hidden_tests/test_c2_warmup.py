"""Tests for the warmup parameter in run_sweep.

Key invariants
--------------
1. warmup=0 (default) gives the same results as calling with no warmup arg.
2. When warmup=N, strategy has seen N candles before trading starts, so
   indicators warm up sooner than a cold start on the same subset.
3. ValueError if warmup >= len(candles).
4. start_equity in every result row still equals starting_cash.
"""

import pytest
from tradingbot.data.feed import synthetic_candles
from tradingbot.risk.manager import RiskManager
from tradingbot.sweep import run_sweep


CANDLES = synthetic_candles(300, seed=42)


def test_warmup_param_accepted():
    rows = run_sweep(CANDLES, RiskManager(), warmup=0)
    assert len(rows) > 0


def test_warmup_zero_matches_no_warmup():
    rows_default = run_sweep(CANDLES, RiskManager())
    rows_zero    = run_sweep(CANDLES, RiskManager(), warmup=0)
    returns_default = [round(r.result.return_pct, 6) for r in rows_default]
    returns_zero    = [round(r.result.return_pct, 6) for r in rows_zero]
    assert returns_default == returns_zero


def test_start_equity_always_starting_cash():
    rows = run_sweep(CANDLES, RiskManager(), starting_cash=5_000, warmup=30)
    assert all(r.result.start_equity == 5_000 for r in rows)


def test_warmup_too_large_raises():
    with pytest.raises(ValueError):
        run_sweep(synthetic_candles(50, seed=1), RiskManager(), warmup=50)


def test_warmup_strategy_state_preserved():
    """Warmup-N run on all candles must beat cold run on candles[N:].

    With warmup=slow, the strategy has full indicators the moment trading
    begins on candle slow+1.  A cold run on candles[slow:] needs another
    `slow` candles before it can trade — so the warmed run must have
    at least as many trades on the same effective candle window.
    """
    fast, slow = 5, 20          # (5,20) is in the sweep grid
    warmup = slow

    rows_warm = run_sweep(CANDLES, RiskManager(), warmup=warmup)
    # Cold run on the same effective candles (candles[warmup:]) — no warmup state
    rows_cold = run_sweep(CANDLES[warmup:], RiskManager())

    def _sma_trades(rows):
        for r in rows:
            if r.strategy == "sma_cross" and f"fast={fast}" in r.params and f"slow={slow}" in r.params:
                return r.result.trades
        return None

    warm_trades = _sma_trades(rows_warm)
    cold_trades = _sma_trades(rows_cold)
    assert warm_trades is not None
    assert cold_trades is not None
    assert warm_trades >= cold_trades, (
        f"warmed run ({warm_trades} trades) should have >= trades than "
        f"cold run on same window ({cold_trades} trades); "
        "strategy state may not be preserved across warmup"
    )
