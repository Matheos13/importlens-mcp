import pytest
from tradingbot.risk.manager import RiskManager


def test_trailing_stop_pct_field_exists():
    rm = RiskManager()
    assert hasattr(rm, "trailing_stop_pct")
    assert rm.trailing_stop_pct is None


def test_trail_stop_price_basic():
    rm = RiskManager(trailing_stop_pct=0.05)
    assert rm.trail_stop_price(100.0) == pytest.approx(95.0)


def test_trail_stop_price_scales_with_peak():
    rm = RiskManager(trailing_stop_pct=0.10)
    assert rm.trail_stop_price(200.0) == pytest.approx(180.0)
    assert rm.trail_stop_price(300.0) == pytest.approx(270.0)


def test_trail_stop_price_none_raises():
    rm = RiskManager()
    with pytest.raises(ValueError, match="trailing stop not configured"):
        rm.trail_stop_price(100.0)


def test_existing_api_unaffected():
    rm = RiskManager(risk_per_trade=0.02, stop_loss_pct=0.05)
    assert rm.position_size(10_000, 100) == pytest.approx(40.0)
    assert rm.stop_hit(100, 94) is True
    assert rm.stop_hit(100, 97) is False
    assert rm.stop_price(100) == pytest.approx(95.0)  # 100 * (1 - 0.05)
