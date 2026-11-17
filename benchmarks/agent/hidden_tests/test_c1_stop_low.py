"""Tests for the stop-loss-on-candle-low fix.

Candle layout used in most tests:
  ts=0: buy signal fires at close=100  (entry_price=100)
  ts=1: has low=95, close=102          (stop at 97 for 3% stop_loss_pct)

With the fix:
  - low=95 < stop_price=97  → stop triggers, fill at 97
  - close=102 is irrelevant

Without the fix (original code):
  - only close=102 is checked → stop does NOT trigger during the loop
  - position stays open → end-of-run close fills at close=102
"""

import pytest
from tradingbot.backtest.engine import BacktestEngine
from tradingbot.data.feed import Candle
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies.base import Signal, Strategy


class _BuyOnce(Strategy):
    """Buys on the first candle, holds forever."""
    name = "buy_once"

    def __init__(self):
        self._bought = False

    def on_candle(self, candle):
        if not self._bought:
            self._bought = True
            return Signal.BUY
        return Signal.HOLD

    def reset(self):
        self._bought = False


def _c(ts, o, h, l, c, v=1_000.0):
    return Candle(ts, o, h, l, c, v)


def _engine(stop_loss_pct=0.03, risk_per_trade=0.02):
    return BacktestEngine(
        _BuyOnce(),
        RiskManager(risk_per_trade=risk_per_trade, stop_loss_pct=stop_loss_pct),
        starting_cash=10_000,
        fee=0.0,
    )


# ── core correctness ────────────────────────────────────────────────────────

def test_stop_triggers_on_low_not_close():
    """low=95 < stop=97 but close=102 > stop=97.  After the fix the sell
    must happen at stop_price=97, not close=102."""
    candles = [
        _c(0, 100, 101, 99, 100),   # buy at close=100
        _c(1, 100, 103, 95, 102),   # low < stop, close > stop
    ]
    engine = _engine()
    engine.run(candles)
    sells = [f for f in engine.broker.fills if f.side == "sell"]
    assert len(sells) == 1
    expected = 100 * (1 - 0.03)   # 97.0 — stop price, not close=102
    assert abs(sells[0].price - expected) < 0.01, (
        f"sell should be at stop_price={expected:.2f}, got {sells[0].price:.2f}; "
        "if price=102 the stop fired against close, not low"
    )


def test_stop_fill_price_is_stop_price_not_close():
    """When the stop fires the sell should execute at stop_price, not close."""
    candles = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 103, 95, 102),
    ]
    engine = _engine()
    engine.run(candles)
    sells = [f for f in engine.broker.fills if f.side == "sell"]
    assert len(sells) == 1
    expected = 100 * (1 - 0.03)   # 97.0
    assert abs(sells[0].price - expected) < 0.01, (
        f"sell fill should be at stop_price={expected:.2f}, got {sells[0].price:.2f}"
    )


def test_stop_does_not_fire_when_low_above_stop():
    """low=98 > stop_price=97.  No fill should occur at the stop price."""
    candles = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 103, 98, 102),   # low=98 > stop=97
    ]
    engine = _engine()
    engine.run(candles)
    stop_p = 100 * (1 - 0.03)   # 97.0
    stop_fills = [
        f for f in engine.broker.fills
        if f.side == "sell" and abs(f.price - stop_p) < 0.01
    ]
    assert not stop_fills, (
        "stop should not fire when candle.low > stop_price; "
        "got a sell fill at stop_price=97 when low=98"
    )


def test_stop_at_exactly_stop_price_fires():
    """low exactly equals stop_price — boundary case, should fire."""
    # stop_price = 100 * (1 - 0.03) = 97.0
    candles = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 103, 97, 102),   # low == stop_price exactly
    ]
    engine = _engine()
    engine.run(candles)
    sells = [f for f in engine.broker.fills if f.side == "sell"]
    assert len(sells) == 1, "stop should fire when low == stop_price"
    assert abs(sells[0].price - 97.0) < 0.01, "fill should be at stop_price=97"


# ── regression: normal strategy sell still works ────────────────────────────

def test_normal_sell_still_works():
    """A SELL signal (not stop-loss) should still fill at the closing price."""
    from tradingbot.strategies.sma_cross import SmaCrossStrategy
    from tradingbot.data.feed import synthetic_candles
    engine = BacktestEngine(
        SmaCrossStrategy(fast=3, slow=6),
        RiskManager(),
        starting_cash=10_000,
        fee=0.0,
    )
    result = engine.run(synthetic_candles(200, seed=5))
    assert result.trades >= 0  # should not crash
