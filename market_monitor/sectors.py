"""板块与龙头分析（P1 / F2.2–F2.6 / §5.5–§5.6）。

- 进攻/防守判定：状态机 + 相对强弱 RS + 资金流 + 换手活跃度 → 四维加权打分 → 进攻 Top5 / 防守 Top5
- 龙头股识别：情绪龙头（涨幅/量比换手/资金流）+ 行业龙头（市值/成交额/ROE）
- 板块择时：板块指数复用 P0 状态机 + 信号引擎
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import Config
from .data.sector_source import Sector, SectorStore
from .regime import STATE_LABELS, compute_regime
from .signals import generate_signals

log = logging.getLogger("market_monitor.sectors")

STATE_SCORE = {"bull": 1.0, "range": 0.5, "transition": 0.3, "bear": 0.0}


@dataclass
class Leader:
    code: str
    name: str
    kind: str          # 情绪龙头 | 行业龙头
    score: float
    metrics: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "kind": self.kind,
                "score": round(self.score, 3), "metrics": self.metrics}


@dataclass
class SectorAnalysis:
    code: str
    name: str
    state: str
    weekly_state: str
    rs_20: Optional[float]
    rs_60: Optional[float]
    corr: Optional[float]
    beta: Optional[float]
    vol_ratio: Optional[float]
    mf_proxy: Optional[float]
    trend_score: float = 0.0
    rs_score: float = 0.0
    mf_score: float = 0.0
    turn_score: float = 0.0
    offensive_score: float = 0.0
    defensive_score: float = 0.0
    signals: List[dict] = field(default_factory=list)
    leaders: List[Leader] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "state": self.state,
            "weekly_state": self.weekly_state, "rs_20": self.rs_20, "rs_60": self.rs_60,
            "corr": self.corr, "beta": self.beta, "vol_ratio": self.vol_ratio,
            "mf_proxy": self.mf_proxy,
            "offensive_score": self.offensive_score, "defensive_score": self.defensive_score,
            "signals": self.signals, "leaders": [l.to_dict() for l in self.leaders],
        }


def _ret(df: pd.DataFrame, n: int) -> Optional[float]:
    if df is None or len(df) < n + 1:
        return None
    c = df["close"]
    if pd.isna(c.iloc[-1]) or pd.isna(c.iloc[-n - 1]) or c.iloc[-n - 1] == 0:
        return None
    return float(c.iloc[-1] / c.iloc[-n - 1] - 1.0)


def _beta(a: pd.Series, b: pd.Series) -> Optional[float]:
    m = pd.concat([a, b], axis=1).dropna()
    if len(m) < 20 or m.iloc[:, 1].var() == 0:
        return None
    return float(m.iloc[:, 0].cov(m.iloc[:, 1]) / m.iloc[:, 1].var())


def _minmax(values: List[float]) -> List[float]:
    vals = [v for v in values if v is not None and not pd.isna(v)]
    if not vals:
        return [0.0] * len(values)
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span == 0:
        return [0.5 if v is not None and not pd.isna(v) else 0.0 for v in values]
    return [0.0 if v is None or pd.isna(v) else (v - lo) / span for v in values]


def analyze_sector(sector: Sector, bench_df: pd.DataFrame, config: Config,
                   store: Optional[SectorStore] = None, start: str = "", end: str = "") -> SectorAnalysis:
    """单个板块分析：状态、RS、相关性、量比、资金流代理、四维分（归一化在 rank_sectors 完成）。"""
    en = compute_regime(sector.index_df, config.regime, config.indicators)
    state = str(en["state"].iloc[-1])
    weekly = str(en["weekly_state"].iloc[-1])

    ret20 = _ret(sector.index_df, 20)
    ret60 = _ret(sector.index_df, 60)
    bre20 = _ret(bench_df, 20)
    bre60 = _ret(bench_df, 60)
    rs_20 = (ret20 - bre20) if (ret20 is not None and bre20 is not None) else None
    rs_60 = (ret60 - bre60) if (ret60 is not None and bre60 is not None) else None

    rets_a = sector.index_df["close"].pct_change().tail(60)
    rets_b = bench_df["close"].pct_change().tail(60)
    corr20_a = sector.index_df["close"].pct_change().tail(20)
    corr20_b = bench_df["close"].pct_change().tail(20)
    corr = float(corr20_a.corr(corr20_b)) if len(corr20_a.dropna()) > 5 else None
    beta = _beta(rets_a, rets_b)

    vol = sector.index_df["volume"]
    vol_ma = vol.rolling(20, min_periods=5).mean().iloc[-1]
    vol_ratio = (float(vol.iloc[-1] / vol_ma) if vol_ma and not pd.isna(vol_ma) and vol_ma > 0 else None)

    # simplify: 板块资金流用「近 5 日动量 × 量比」代理；真实主力资金（按成员 moneyflow 汇总）后续加强
    ret5 = _ret(sector.index_df, 5)
    mf_proxy = (np.sign(ret5) * min(abs(vol_ratio or 0.0), 3.0)) if ret5 is not None else None

    signals = [s.to_dict() for s in generate_signals(en, config.regime, config.indicators, config.cost)]
    return SectorAnalysis(
        code=sector.code, name=sector.name, state=state, weekly_state=weekly,
        rs_20=rs_20, rs_60=rs_60, corr=corr, beta=beta, vol_ratio=vol_ratio, mf_proxy=mf_proxy,
        signals=signals,
    )


def rank_sectors(analyses: List[SectorAnalysis], config: Config) -> tuple[List[SectorAnalysis], List[SectorAnalysis]]:
    """四维打分归一化 + 加权 → 进攻榜 / 防守榜（§5.5）。"""
    sp = config.sector
    trend = [STATE_SCORE.get(a.state, 0.3) for a in analyses]
    rs = [((a.rs_20 or 0) * 0.5 + (a.rs_60 or 0) * 0.5) for a in analyses]
    mf = [a.mf_proxy or 0.0 for a in analyses]
    turn = [a.vol_ratio or 1.0 for a in analyses]

    trend_s, rs_s, mf_s, turn_s = _minmax(trend), _minmax(rs), _minmax(mf), _minmax(turn)
    for a, t, r, m, tu in zip(analyses, trend_s, rs_s, mf_s, turn_s):
        a.trend_score, a.rs_score, a.mf_score, a.turn_score = t, r, m, tu
        a.offensive_score = (sp.w_trend * t + sp.w_rs * r + sp.w_mf * m + sp.w_turn * tu)

    # 防守分：抗跌 + 低 Beta + 资金稳 + 换手萎缩（§5.5 防守维度）
    def _defensive(a: SectorAnalysis) -> float:
        resist = 1.0 - min(abs(a.rs_20 or 0.0) / 0.10, 1.0) if (a.rs_20 or 0) < 0 else 0.0
        low_beta = float(np.clip(1.0 - (a.beta if a.beta is not None else 1.0), 0.0, 1.0))
        stable = 1.0 - min(abs(a.mf_proxy or 0.0) / 3.0, 1.0)
        shrink = float(np.clip(1.5 - (a.vol_ratio or 1.0), 0.0, 1.0))
        return 0.25 * resist + 0.25 * low_beta + 0.25 * stable + 0.25 * shrink

    for a in analyses:
        a.defensive_score = _defensive(a)

    offensive = sorted(analyses, key=lambda a: a.offensive_score, reverse=True)[: sp.top_n]
    defensive = sorted(analyses, key=lambda a: a.defensive_score, reverse=True)[: sp.top_n]
    return offensive, defensive


def identify_leaders(sector: Sector, store: SectorStore, config: Config,
                     start: str, end: str) -> List[Leader]:
    """龙头识别（§5.6）：情绪龙头（近5/20日涨幅、量比换手、资金流代理）+ 行业龙头（成交额/市值）。

    仅处理 A 股代码（ths_member 可能混入境外股，pro_bar 不支持，跳过）。
    """
    import re

    sp = config.sector
    a_share = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
    members = [c for c in sector.members if a_share.match(c)][: sp.member_limit]
    rows: List[dict] = []
    for code in members:
        df = store.stock_daily(code, start, end)
        if df is None or len(df) < 21:
            continue
        ret5 = _ret(df, 5)
        ret20 = _ret(df, 20)
        vol_ma = df["volume"].rolling(20, min_periods=5).mean().iloc[-1]
        vol_ratio = float(df["volume"].iloc[-1] / vol_ma) if vol_ma and vol_ma > 0 else 1.0
        amount = float((df["close"] * df["volume"]).tail(20).mean())
        mf_proxy = np.sign(ret5 or 0.0) * min(vol_ratio, 3.0) if ret5 is not None else 0.0
        basic = store.stock_basic(code)
        rows.append({
            "code": code, "name": sector.member_names.get(code, code),
            "ret5": ret5 or 0.0, "ret20": ret20 or 0.0,
            "vol_ratio": vol_ratio, "mf": mf_proxy, "amount": amount,
            "total_mv": basic.get("total_mv", 0.0),
        })
    if not rows:
        return []

    r5 = _minmax([r["ret5"] for r in rows])
    r20 = _minmax([r["ret20"] for r in rows])
    vr = _minmax([r["vol_ratio"] for r in rows])
    mf = _minmax([r["mf"] for r in rows])
    for row, a, b, c, d in zip(rows, r5, r20, vr, mf):
        row["emo_score"] = 0.4 * a + 0.2 * b + 0.2 * c + 0.2 * d

    leaders: List[Leader] = []
    for row in sorted(rows, key=lambda r: r["emo_score"], reverse=True)[:2]:
        leaders.append(Leader(row["code"], row["name"], "情绪龙头", row["emo_score"],
                              {"近5日涨幅": round(row["ret5"], 4), "量比": round(row["vol_ratio"], 2),
                               "资金流代理": round(row["mf"], 2)}))
    ind = max(rows, key=lambda r: r["total_mv"] or r["amount"])
    if ind["total_mv"] or ind["amount"]:
        leaders.append(Leader(ind["code"], ind["name"], "行业龙头", 1.0,
                              {"成交额均值": round(ind["amount"], 0), "总市值": round(ind["total_mv"], 0)}))
    return leaders[:3]


def run_sector_analysis(config: Config, source: str = "auto", start: str = "",
                        end: str = "", limit: Optional[int] = None,
                        bench_symbol: str = "sh000001") -> dict:
    """端到端板块分析：基准指数 + 板块列表 → 逐板块分析 → 进攻/防守榜 → 龙头。

    source: auto|tushare|akshare|synthetic；limit 限制板块数量（默认全量，建议演示时限制）。
    """
    import datetime as dt

    from .data import DataManager, default_start, generate_synthetic
    from .data.sector_source import make_sector_store

    end = (end or dt.date.today().strftime("%Y%m%d")).replace("-", "")
    start = start or config.data.start or default_start(end)

    bench_df = None
    if source != "synthetic":
        try:
            dm = DataManager(config)
            bench_df = dm.get(bench_symbol, "tushare", start, end)
        except Exception as e:  # noqa: BLE001
            log.warning("基准指数获取失败（%s），改用合成：%s", bench_symbol, e)
    if bench_df is None or len(bench_df) < 60:
        bench_df = generate_synthetic(bench_symbol, start, end)

    store = make_sector_store(config, source)
    sectors = store.list_sectors(start, end, limit)
    if not sectors:
        return {"date": end, "analyses": [], "offensive": [], "defensive": []}

    analyses = [analyze_sector(s, bench_df, config, store, start, end) for s in sectors]
    offensive, defensive = rank_sectors(analyses, config)

    # 对进攻/防守榜板块识别龙头（避免全量拉个股行情）
    for a in offensive + defensive:
        sec = next((s for s in sectors if s.code == a.code), None)
        if sec is not None:
            a.leaders = identify_leaders(sec, store, config, start, end)

    return {"date": end, "analyses": analyses, "offensive": offensive,
            "defensive": defensive, "sectors": sectors}
