"""市场状态机（F1.4 / §5.2）。

单一判定流程：
    先按趋势条件组合粗判方向 → ADX 作确认权重 → 带宽分位佐证 → 无法归类一律「过渡态」。

状态层级分离：
    - state（日线确认状态，经防抖）：信号触发用；
    - weekly_state（周线趋势）：仅作强确认/加减仓依据，报告分离展示。

无未来函数：周线分类只用已收盘周（W-FRI resample + ffill），日线状态只用 ≤t 数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import IndicatorParams, RegimeParams
from .indicators.technical import add_all_indicators, resample_weekly, sma


def classify_weekly(df_w: pd.DataFrame, ma_mid: int = 20, ma_slow: int = 60) -> pd.Series:
    """对周线 DataFrame 逐周分类：bullish / bearish / neutral。

    多头：价 > W_MA20 且 W_MA20 > W_MA60 且 W_MA20 上行；
    空头：镜像反向；否则中性。
    """
    w_ma20 = sma(df_w["close"], ma_mid)
    w_ma60 = sma(df_w["close"], ma_slow)
    rising = w_ma20 > w_ma20.shift(1)

    bullish = (df_w["close"] > w_ma20) & (w_ma20 > w_ma60) & rising
    bearish = (df_w["close"] < w_ma20) & (w_ma20 < w_ma60) & (~rising)

    out = pd.Series("neutral", index=df_w.index, dtype=object)
    out[bullish] = "bullish"
    out[bearish] = "bearish"
    # 早期数据不足（W_MA60 为 NaN）时保持 neutral
    out[(w_ma60.isna())] = "neutral"
    return out


def compute_daily_score(df: pd.DataFrame) -> pd.Series:
    """日线打分（§5.2 输入之一），取值 [-3, 3]。

    +1/-1 分别对应：MA5>MA10>MA20（镜像反向）、MACD 金叉态（DIF>DEA，镜像）、价 > BOLL 中轨（镜像）。
    """
    score = pd.Series(0, index=df.index, dtype=int)

    ma_up = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])
    ma_dn = (df["ma5"] < df["ma10"]) & (df["ma10"] < df["ma20"])
    score = score + np.where(ma_up, 1, 0) + np.where(ma_dn, -1, 0)

    score = score + np.where(df["dif"] > df["dea"], 1, 0) + np.where(df["dif"] < df["dea"], -1, 0)
    score = score + np.where(df["close"] > df["boll_mid"], 1, 0) + np.where(df["close"] < df["boll_mid"], -1, 0)

    return score.astype(int)


def _decide(weekly_trend: str, daily_score: float, adx: float, bw_pct: float,
            close: float, ma60: float, ma60_rising: bool, p: RegimeParams) -> str:
    """单一判定流程（§5.2 状态定义表）。无法归类 → transition。"""
    if weekly_trend == "bullish" and adx >= p.adx_trend and close > ma60 and ma60_rising:
        return "bull"
    if weekly_trend == "bearish" and adx >= p.adx_trend and close < ma60 and not ma60_rising:
        return "bear"
    # 震荡市：弱趋势 + 低带宽 + 打分中性（周线方向近似为 0）
    if adx < p.adx_range and bw_pct < p.bw_range_pct and abs(daily_score) <= 1:
        return "range"
    return "transition"


def debounce(states: pd.Series, confirm_bars: int) -> pd.Series:
    """防抖：仅当新状态连续 confirm_bars 根 K 线一致时才切换，否则沿用上一确认状态。"""
    confirmed = []
    current = states.iloc[0] if len(states) else "transition"
    pending = None
    count = 0
    for s in states:
        if s == current:
            pending = None
            count = 0
        else:
            pending = pending if pending == s else s
            count = count + 1 if pending == s else 1
            if count >= confirm_bars:
                current = s
                pending = None
                count = 0
        confirmed.append(current)
    return pd.Series(confirmed, index=states.index)


def compute_regime(df: pd.DataFrame, params: RegimeParams | None = None,
                   ind: IndicatorParams | None = None) -> pd.DataFrame:
    """在日线 DataFrame 上计算状态机，返回带状态列的副本。

    新增列：weekly_state, daily_score, state_raw, state（日线确认状态，防抖后）。
    """
    p = params or RegimeParams()
    ip = ind or IndicatorParams()
    out = add_all_indicators(df, ind)

    # 周线趋势（仅用已收盘周）
    df_w = resample_weekly(df)
    weekly_cls = classify_weekly(df_w, ip.ma_mid, ip.ma_slow)
    weekly_map = weekly_cls.reindex(out.index, method="ffill")

    out["daily_score"] = compute_daily_score(out)
    out["ma60_rising"] = out["ma60"] > out["ma60"].shift(1)

    def row_decide(r):
        ma60_rising = bool(r["ma60_rising"]) if not pd.isna(r["ma60_rising"]) else False
        return _decide(
            weekly_map.loc[r.name],
            float(r["daily_score"]),
            float(r["adx"]),
            float(r["bw_pct"]),
            float(r["close"]),
            float(r["ma60"]),
            ma60_rising,
            p,
        )

    raw = out.apply(row_decide, axis=1)
    out["state_raw"] = raw
    out["state"] = debounce(raw, p.confirm_bars)

    # 周线状态（3 类，仅展示用）：bullish→bull, bearish→bear, neutral→range
    out["weekly_state"] = weekly_map.map({"bullish": "bull", "bearish": "bear", "neutral": "range"})

    return out


STATE_LABELS = {
    "bull": "牛市",
    "bear": "熊市",
    "range": "震荡市",
    "transition": "过渡态",
}
