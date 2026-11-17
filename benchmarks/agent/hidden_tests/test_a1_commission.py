import pytest
from tradingbot.risk.manager import RiskManager


def test_commission_pct_field_exists():
    rm = RiskManager()
    assert hasattr(rm, "commission_pct")
    assert rm.commission_pct == pytest.approx(0.001)


def test_commission_reduces_size():
    rm = RiskManager(risk_per_trade=0.02, stop_loss_pct=0.05, commission_pct=0.001)
    size = rm.position_size(equity=10_000, price=100)
    # risk_cash = 200, denom = 100*0.05 + 2*100*0.001 = 5.2
    assert abs(size - 200 / 5.2) < 0.01


def test_zero_commission_matches_original():
    rm = RiskManager(risk_per_trade=0.02, stop_loss_pct=0.05, commission_pct=0.0)
    assert rm.position_size(10_000, 100) == pytest.approx(40.0)


def test_no_args_still_works():
    rm = RiskManager()
    size = rm.position_size(10_000, 100)
    # Default commission reduces size slightly vs commission=0
    rm0 = RiskManager(risk_per_trade=0.02, stop_loss_pct=0.03, commission_pct=0.0)
    assert 0 < size < rm0.position_size(10_000, 100) * 1.01


def test_zero_price_guard_still_works():
    rm = RiskManager(commission_pct=0.001)
    assert rm.position_size(10_000, 0) == 0.0


def test_commission_larger_values():
    rm = RiskManager(risk_per_trade=0.02, stop_loss_pct=0.10, commission_pct=0.005)
    size = rm.position_size(10_000, 100)
    expected = 200 / (100 * 0.10 + 2 * 100 * 0.005)  # 200 / 11 ≈ 18.18
    assert abs(size - expected) < 0.01
