"""分状态 BOLL 择时信号 + 入场计划（F1.5 / F1.6 / §5.3 / §5.4）。

只产生**入场信号**（多头，P0）；离场由回测引擎按止损/目标/吊灯机械执行。
入场价统一为「信号次日开盘价」（回测 shift(1) 填充），杜绝未来函数。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional

import pandas as pd

from .config import CostModel, IndicatorParams, RegimeParams


@dataclass
class SignalRecord:
    """一条入场信号。"""
    date: object
    action: str            # buy | add | bull_start
    state: str             # 触发时的市场状态
    reason: str
    close: float           # 信号日收盘（信号确认价）
    stop: float            # 止损位（信号日收盘锚定的绝对价位）
    target: float          # 止盈参考位（牛/震荡均用上轨）
    rr_estimate: float     # 用信号日收盘作入场代理估算的盈亏比
    skipped: bool          # 盈亏比 < min_rr → 不出手

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = str(self.date)
        return d


def _vol_surge(cur: pd.Series, prev: pd.Series, p: IndicatorParams) -> bool:
    """放量：当日量 > volume_surge × 前 20 日均量（用 prev 避免当日自含）。"""
    ma = prev.get("vol_ma20")
    if ma is None or pd.isna(ma) or ma <= 0:
        return False
    return float(cur["volume"]) > p.volume_surge * float(ma)


def _make_record(date, action, state, reason, close, stop, target, min_rr) -> Optional[SignalRecord]:
    risk = close - stop
    if pd.isna(close) or pd.isna(stop) or pd.isna(target) or risk <= 0:
        return None
    rr = (target - close) / risk
    return SignalRecord(
        date=date, action=action, state=state, reason=reason,
        close=float(close), stop=float(stop), target=float(target),
        rr_estimate=float(rr),
        # 1e-9 容差：1.5R 规划目标经浮点除法可能得到 1.49999…，不应误判为不足
        skipped=rr < min_rr - 1e-9,
    )


def _bull_entry(cur: pd.Series, prev: pd.Series, ip: IndicatorParams, cost: CostModel) -> Optional[SignalRecord]:
    """牛市入场：回踩中轨企稳 → buy；放量突破上轨 → add；收口后放量突破上轨 → bull_start。

    设计决策（simplify:）：
        牛市为趋势跟踪（trailing，§5.3「不设上轨止盈、让利润奔跑」），若拿固定上轨当止盈
        参考，盈亏比会天然 <1.5（回踩中轨时 上轨−价≈2σ，而 2×ATR≈3.2σ → rr≈0.6；
        突破上轨时上轨还在入场价下方 → rr 为负），牛市信号将永远被「rr<1.5 不出手」过滤掉。
        因此止盈参考 = max(上轨, 入场 + min_rr×风险)：上轨足够远时用真实上轨（rr>1.5），
        否则按趋势单最低可接受 1.5R 计（收益开敞，过滤仅保证止损布置合理）。
    """
    risk = 2.0 * cur["atr"]
    target = max(cur["boll_upper"], cur["close"] + cost.min_rr * risk)

    squeeze_break = (
        not pd.isna(prev.get("bw_pct")) and float(prev["bw_pct"]) < 0.20
        and cur["close"] > cur["boll_upper"] and _vol_surge(cur, prev, ip)
    )

    if squeeze_break:
        return _make_record(cur.name, "bull_start", "bull", "收口后放量突破上轨（最高置信）",
                            cur["close"], cur["close"] - risk, target, cost.min_rr)

    vol_break = cur["close"] > cur["boll_upper"] and _vol_surge(cur, prev, ip)
    if vol_break:
        return _make_record(cur.name, "add", "bull", "放量突破上轨加仓",
                            cur["close"], cur["close"] - risk, target, cost.min_rr)

    mid_rising = cur["boll_mid"] > prev["boll_mid"]
    bullish_candle = cur["close"] > cur["open"]
    reclaim_mid = (prev["close"] < prev["boll_mid"]) and (cur["close"] >= cur["boll_mid"])
    rsi_up = cur["rsi"] > prev["rsi"]
    if mid_rising and bullish_candle and reclaim_mid and rsi_up:
        return _make_record(cur.name, "buy", "bull", "回踩中轨企稳（中轨上行+阳线收回+RSI拐头）",
                            cur["close"], cur["close"] - risk, target, cost.min_rr)
    return None


def _range_entry(cur: pd.Series, prev: pd.Series, ip: IndicatorParams, cost: CostModel) -> Optional[SignalRecord]:
    """震荡市入场：触下轨后收回轨内阳线 + RSI<30 拐头 / KDJ 金叉。"""
    touch_lower = cur["low"] <= cur["boll_lower"]
    reclaimed = cur["close"] > cur["boll_lower"]
    bullish_candle = cur["close"] > cur["open"]
    rsi_trigger = (cur["rsi"] < ip.rsi_oversold) and (cur["rsi"] > prev["rsi"])
    kdj_cross = (cur["kdj_k"] > cur["kdj_d"]) and (prev["kdj_k"] <= prev["kdj_d"])

    if touch_lower and reclaimed and bullish_candle and (rsi_trigger or kdj_cross):
        return _make_record(cur.name, "buy", "range", "触下轨收回+RSI拐头/KDJ金叉",
                            cur["close"], cur["boll_lower"], cur["boll_upper"], cost.min_rr)
    return None


def generate_signals(df: pd.DataFrame, params: RegimeParams | None = None,
                     ind: IndicatorParams | None = None, cost: CostModel | None = None) -> List[SignalRecord]:
    """在**已含状态列**的 DataFrame 上生成入场信号（df 需先经 compute_regime）。"""
    ip = ind or IndicatorParams()
    cost = cost or CostModel()
    prev = df.shift(1)
    records: List[SignalRecord] = []

    for idx in range(len(df)):
        cur = df.iloc[idx]
        if pd.isna(cur.get("state")):
            continue
        st = cur["state"]
        if st == "bull":
            rec = _bull_entry(cur, prev.iloc[idx], ip, cost)
        elif st == "range":
            rec = _range_entry(cur, prev.iloc[idx], ip, cost)
        else:  # bear / transition：不做多
            rec = None
        if rec is not None:
            records.append(rec)
    return records


def volatility_state(bw_pct: float) -> str:
    """波动状态（§5.4）：收口⚠️ / 扩张 / 正常。"""
    if pd.isna(bw_pct):
        return "—"
    if bw_pct < 0.20:
        return "收口⚠️"
    if bw_pct > 0.60:
        return "扩张"
    return "正常"


def detect_alerts(df: pd.DataFrame) -> List[str]:
    """对最后一根 K 线（= 报告目标日）做波动预警（§5.4）。"""
    if len(df) == 0:
        return []
    r = df.iloc[-1]
    alerts = []
    if not pd.isna(r.get("bw_pct")) and r["bw_pct"] < 0.20:
        alerts.append("BOLL 收口变盘预警（带宽分位 <20%）")
    if "hv" in df and "atr" in df:
        hv_ma = df["hv"].rolling(20, min_periods=20).mean().iloc[-1]
        atr_ma = df["atr"].rolling(20, min_periods=20).mean().iloc[-1]
        if not pd.isna(hv_ma) and hv_ma > 0 and r["hv"] > 1.5 * hv_ma:
            alerts.append("HV 异常放大（较 20 日均值 >50%）")
        if not pd.isna(atr_ma) and atr_ma > 0 and r["atr"] > 1.5 * atr_ma:
            alerts.append("ATR 异常放大（较 20 日均值 >50%）")
    return alerts
