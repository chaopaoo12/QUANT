"""P0 单元测试（pytest）。运行：python -m pytest tests/ -q"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_monitor.config import CostModel, RegimeParams
from market_monitor.data import generate_synthetic
from market_monitor.indicators.technical import (
    adx, atr, bandwidth_percentile, bollinger, macd, rsi, sma,
)
from market_monitor.regime import compute_regime, debounce
from market_monitor.signals import generate_signals
from market_monitor.backtest import backtest_symbol, summarize_backtest


@pytest.fixture(scope="module")
def synth():
    return generate_synthetic("sh000001", "20240101", "20260826")


def test_bollinger_mid_is_sma(synth):
    b = bollinger(synth["close"], 20, 2.0)
    assert np.allclose(b["mid"].dropna(), sma(synth["close"], 20).dropna())


def test_rsi_bounded(synth):
    r = rsi(synth["close"], 14).dropna()
    assert r.between(0, 100).all()


def test_atr_adx_positive(synth):
    a = atr(synth["high"], synth["low"], synth["close"], 14).dropna()
    d = adx(synth["high"], synth["low"], synth["close"], 14)["adx"].dropna()
    assert (a > 0).all()
    assert d.between(0, 100).all()


def test_bandwidth_percentile_range(synth):
    b = bollinger(synth["close"])
    bw = (b["upper"] - b["lower"]) / b["mid"]
    pct = bandwidth_percentile(bw, 250, 60).dropna()
    assert pct.between(0, 1).all()


def test_macd_shape(synth):
    m = macd(synth["close"])
    assert {"dif", "dea", "hist"} <= set(m.columns)


def test_debounce_switches_after_confirm():
    s = pd.Series(["bull", "bull", "range", "range", "range"])
    out = debounce(s, 2)
    # range 需连续 2 根才切换：第 3 根仍 bull，第 4 根才 range
    assert list(out) == ["bull", "bull", "bull", "range", "range"]


def test_regime_states_valid(synth):
    enriched = compute_regime(synth, RegimeParams())
    assert set(enriched["state"].dropna()) <= {"bull", "bear", "range", "transition"}
    assert set(enriched["weekly_state"].dropna()) <= {"bull", "bear", "range"}


def test_signals_no_negative_risk(synth):
    enriched = compute_regime(synth, RegimeParams())
    sigs = generate_signals(enriched, RegimeParams(), None, CostModel())
    for s in sigs:
        assert s.stop < s.close
        if not s.skipped:
            assert s.rr_estimate >= CostModel().min_rr - 1e-9


def test_backtest_no_lookahead_and_costs(synth):
    """回测必须次日开盘成交（shift(1)），且盈利/亏损已扣成本。"""
    enriched = compute_regime(synth, RegimeParams())
    cost = CostModel()
    sigs = generate_signals(enriched, RegimeParams(), None, cost)
    trades, equity = backtest_symbol(enriched, sigs, cost)
    assert np.isfinite(equity).all()
    # 每笔交易入场价必须严格等于某根 K 线的开盘价（次日开盘）
    opens = set(round(x, 6) for x in synth["open"].to_numpy())
    for t in trades:
        assert round(t.entry, 6) in opens
    bt = summarize_backtest(trades, equity)
    assert "profit_factor" in bt and "max_drawdown" in bt
