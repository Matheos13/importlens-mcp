import pytest
from tradingbot.backtest.engine import BacktestEngine
from tradingbot.data.feed import synthetic_candles
from tradingbot.risk.manager import RiskManager
from tradingbot.strategies.sma_cross import SmaCrossStrategy


def _engine(**kw):
    return BacktestEngine(
        SmaCrossStrategy(fast=3, slow=6),
        RiskManager(),
        starting_cash=10_000,
        fee=0.0,
        **kw,
    )


def test_max_trades_param_accepted():
    engine = _engine(max_trades=1)
    result = engine.run(synthetic_candles(500, seed=1))
    assert result is not None


def test_max_trades_1_caps_trades():
    result = _engine(max_trades=1).run(synthetic_candles(500, seed=1))
    # 1 completed round-trip; engine may still close a final open position
    assert result.trades <= 2


def test_max_trades_fewer_than_unlimited():
    unlimited = _engine().run(synthetic_candles(500, seed=1))
    limited = _engine(max_trades=1).run(synthetic_candles(500, seed=1))
    assert unlimited.trades > limited.trades, (
        "unlimited run should have more trades than max_trades=1"
    )


def test_max_trades_none_is_default():
    r1 = _engine(max_trades=None).run(synthetic_candles(200, seed=7))
    r2 = _engine().run(synthetic_candles(200, seed=7))
    assert r1.trades == r2.trades


def test_sweep_unaffected():
    from tradingbot.sweep import run_sweep
    rows = run_sweep(synthetic_candles(100, seed=1), RiskManager())
    assert len(rows) > 0


def test_cli_import_unaffected():
    # Importing __main__ should not raise even with the new parameter
    import tradingbot.__main__  # noqa: F401
