import pytest
from tradingbot.broker.paper import Fill, PaperBroker
from tradingbot.backtest.engine import BacktestEngine
from tradingbot.data.feed import synthetic_candles
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies.sma_cross import SmaCrossStrategy


def test_fill_has_ts_field():
    f = Fill(side="buy", price=100.0, qty=1.0)
    assert hasattr(f, "ts")


def test_buy_accepts_ts_kwarg():
    b = PaperBroker(cash=10_000)
    b.buy(100.0, 5.0, ts=42)
    assert b.fills[0].ts == 42


def test_sell_accepts_ts_kwarg():
    b = PaperBroker(cash=10_000)
    b.buy(100.0, 5.0, ts=1)
    b.sell(100.0, 5.0, ts=99)
    assert b.fills[1].ts == 99


def test_backtest_fills_carry_candle_ts():
    candles = synthetic_candles(n=200, seed=1)
    valid_ts = {c.ts for c in candles}
    engine = BacktestEngine(
        SmaCrossStrategy(fast=3, slow=6),
        RiskManager(),
        starting_cash=10_000,
        fee=0.0,
    )
    engine.run(candles)
    assert len(engine.broker.fills) > 0, "no trades made — seed or strategy mismatch"
    for fill in engine.broker.fills:
        assert fill.ts in valid_ts, (
            f"fill.ts={fill.ts} not a valid candle timestamp; "
            "BacktestEngine is not passing ts=candle.ts"
        )


def test_no_zero_ts_in_backtest():
    candles = synthetic_candles(n=200, seed=1)
    engine = BacktestEngine(
        SmaCrossStrategy(fast=3, slow=6),
        RiskManager(),
        starting_cash=10_000,
        fee=0.0,
    )
    engine.run(candles)
    zero_ts = [f for f in engine.broker.fills if f.ts == 0]
    assert not zero_ts, (
        f"{len(zero_ts)} fill(s) have ts=0; "
        "ensure every broker.buy/sell call in BacktestEngine.run passes ts=candle.ts"
    )
