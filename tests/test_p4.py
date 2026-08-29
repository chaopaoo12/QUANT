"""P4 IPO 估值测试（离线）。"""
from __future__ import annotations

from market_monitor.config import Config
from market_monitor.ipo import (
    comparable_valuation, dcf_rough, pricing_judgment, quality_score, run_ipo_analysis,
)


def test_comparable_valuation():
    lo, hi = comparable_valuation(20.0, 1.0, 3.0, 5.0, 5.0, 4.0)
    # PE×EPS=20, PB×BPS=15, PS×SPS=20 → 中位 20，区间 [17, 23]
    assert lo < 20 < hi
    assert abs(lo - 17.0) < 1e-9 and abs(hi - 23.0) < 1e-9


def test_pricing_judgment():
    assert pricing_judgment(25.0, 20.0) == "溢价"       # +25%
    assert pricing_judgment(17.0, 20.0) == "折价"       # -15%
    assert pricing_judgment(19.0, 20.0) == "合理"       # -5% < 10% 容差
    assert pricing_judgment(20.0, 20.0) == "合理"       # 恰好等于可比
    assert pricing_judgment(None, 20.0) == "无法判断"
    assert pricing_judgment(20.0, None) == "无法判断"


def test_dcf_rough():
    # 净利润1亿、CAGR 15%、5年、折现9%、永续2.5%、股本1亿 → 每股 > 发行时净利/股本=1
    v = dcf_rough(1.0, 0.15, years=5, discount=0.09, terminal_growth=0.025, shares=1.0)
    assert v is not None and v > 1.0
    assert dcf_rough(1.0, 0.01, shares=1.0) is None   # CAGR ≤ 永续增长 → 无法粗算


def test_quality_score_bounds():
    assert quality_score(1, 0, 0, 1) >= 99
    assert quality_score(0, 1, 1, 0) <= 1
    assert 0 <= quality_score() <= 100


def test_run_ipo_analysis_synthetic():
    result = run_ipo_analysis(Config(), source="synthetic", limit=10)
    assert len(result["ipos"]) == 10
    for i in result["ipos"]:
        assert i.pricing in ("折价", "溢价", "合理", "无法判断")
        assert i.rating_short in ("积极", "谨慎", "回避")
        assert i.rating_long in ("优选", "一般", "规避")
        assert len(i.risk_factors) >= 1
