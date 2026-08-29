"""P3 财报分析测试（离线）。"""
from __future__ import annotations

import pandas as pd

from market_monitor.financials import market_scan, post_reaction, quadrant


def test_quadrant_classification():
    assert quadrant(0.3, 0.2) == "机会"
    assert quadrant(0.3, 0.8) == "透支"
    assert quadrant(-0.1, 0.2) == "价值陷阱排查"
    assert quadrant(-0.1, 0.8) == "回避"
    assert quadrant(None, 0.2) == "回避"          # 数据缺失保守处理
    assert quadrant(0.3, None) == "回避"


def test_market_scan_synthetic():
    from market_monitor.config import Config

    rows = market_scan(Config(), "synthetic", "20260826", universe_limit=20)
    assert len(rows) == 20
    assert all("quadrant" in r for r in rows)
    assert all(r["quadrant"] in ("机会", "透支", "价值陷阱排查", "回避") for r in rows)


def test_post_reaction_alignment():
    idx = pd.bdate_range("2026-08-01", periods=10)
    closes = pd.Series(range(10, 20), dtype=float, index=idx)
    df = pd.DataFrame({"close": closes})
    # 披露日落在非交易日（周六）→ 顺延到下一个交易日
    r = post_reaction(df, "2026-08-08")
    assert r["1d"] is not None
    assert abs(r["1d"] - (closes.iloc[6] / closes.iloc[5] - 1)) < 1e-9
    # 披露日在数据末尾 → 无法计算
    r2 = post_reaction(df, "2026-08-10")
    assert r2["5d"] is None
