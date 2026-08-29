"""合成行情生成器（离线演示 / 单元测试用，不依赖网络）。

四段式 regime（按日期占比，确定性：同 symbol 同参数输出一致）：
    0–62%   震荡热身（均值回归、低波动）→ 让周线 MA60 成形，ADX 低 → range 态
    62–80%  强多头（强上行漂移）        → 周线/日线 bull（ADX ≥ 25）
    80–88%  震荡（均值回归、低波动）    → range 态（触发下轨买入/上轨止盈）
    88–100% 熊（下行漂移）             → bear 态

设计说明：牛市段必须放在**周线 MA60（60 周 ≈ 300 交易日）成形之后**，
否则前段牛市的周线趋势因 W_MA60 未形成而被判为中性，状态机永远进不了 bull。
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd


def _seed_for(symbol: str) -> int:
    return int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)


def generate_synthetic(symbol: str, start: str, end: str, style: str = "cycle",
                       init_price: float = 100.0) -> pd.DataFrame:
    """生成 [start, end] 区间（工作日）的合成日线。"""
    dates = pd.bdate_range(start, end)
    n = len(dates)
    if n < 2:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    rng = np.random.default_rng(_seed_for(symbol))

    vol_quiet = 0.008   # 震荡段波动（压低 ADX，保证 range 态可达）
    vol_trend = 0.012   # 趋势段波动
    drift_bull = 0.0035  # 牛市日漂移（足以推升 ADX ≥ 25 与周线多头）
    drift_bear = 0.0020  # 熊市日漂移
    meanrev = 0.12      # 均值回归强度

    close = np.empty(n)
    close[0] = init_price
    center = init_price
    bull_start = int(n * 0.62)
    bull_end = int(n * 0.80)
    range_end = int(n * 0.88)
    for i in range(1, n):
        if i < bull_start:
            ret = meanrev * (center - close[i - 1]) / close[i - 1] + vol_quiet * rng.standard_normal()
        elif i < bull_end:
            # 强多头 + 周期性回调（每 18 日插 3 日回调 → 制造「回踩中轨企稳」）
            day = (i - bull_start) % 18
            if day < 3:
                ret = -0.006 + vol_trend * rng.standard_normal()
            else:
                ret = drift_bull + vol_trend * rng.standard_normal()
                center = close[i - 1]
        elif i < range_end:
            # 震荡段：均值回归为主，偶发反弹日
            day = (i - bull_end) % 14
            if day in (4, 5):
                ret = 0.012 + vol_quiet * rng.standard_normal()
            else:
                ret = meanrev * (center - close[i - 1]) / close[i - 1] + vol_quiet * rng.standard_normal()
        else:
            ret = -drift_bear + vol_trend * rng.standard_normal()
        close[i] = close[i - 1] * (1.0 + ret)

    close = pd.Series(close, index=dates)
    prev_close = close.shift(1).fillna(close.iloc[0])

    open_ = prev_close * (1.0 + 0.003 * rng.standard_normal(n))
    body = np.abs(close.values - open_.values)
    high = np.maximum(open_.values, close.values) + body * rng.uniform(0.2, 0.8, n)
    low = np.minimum(open_.values, close.values) - body * rng.uniform(0.2, 0.8, n)
    # 震荡段：每 14 日周期第 0–1 日插入深下影（wick 触下轨、收盘仍近中轨）→ 「触下轨收回」可触发
    for i in range(bull_end, range_end):
        if (i - bull_end) % 14 < 2:
            low[i] = close.iloc[i] - 3.2 * vol_trend * close.iloc[i]

    base_vol = 1_000_000.0
    volume = base_vol * (1.0 + 0.4 * rng.standard_normal(n))
    # 随机放量（约 8% 交易日），便于触发「放量突破上轨」
    surge = rng.random(n) < 0.08
    volume[surge] *= rng.uniform(1.8, 2.5, int(surge.sum()))

    df = pd.DataFrame(
        {
            "open": open_.values,
            "high": high,
            "low": low,
            "close": close.values,
            "volume": np.round(volume),
        },
        index=dates,
    )
    df.index.name = "date"
    return df
