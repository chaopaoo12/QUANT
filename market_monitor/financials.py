"""财报分析（P3 / F3.x / §5.7）。

- 披露检测：forecast（预告）/ express（快报）/ income（正式，ann_date 口径；disclosure_date 仅作预算）
- 龙头板块跟进：进攻榜板块成员新披露 → 超预期占比高=主线强化 / 龙头暴雷=退潮预警
- 全市场扫描：按概念板块聚合景气度 + 景气-估值四象限（单季同比主判）
- 财报后 1/3/5 日涨跌幅（按交易日顺延）

数据源：tushare 主源；source='synthetic' 时用确定性合成基本面（离线演示/测试）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger("market_monitor.financials")

QUADRANT_ORDER = {"机会": 0, "透支": 1, "价值陷阱排查": 2, "回避": 3}


def quadrant(growth: Optional[float], pe_pct: Optional[float],
             high_growth: float = 0.20, low_pe_pct: float = 0.50) -> str:
    """景气-估值四象限（§5.7）：增速(单季同比) × PE 历史分位。数据缺失 → 回避（保守）。"""
    if growth is None or pe_pct is None or pd.isna(growth) or pd.isna(pe_pct):
        return "回避"
    high = growth >= high_growth
    low = pe_pct <= low_pe_pct
    if high and low:
        return "机会"
    if high:
        return "透支"
    if low:
        return "价值陷阱排查"
    return "回避"


def _synthetic_fundamental(code: str) -> dict:
    """确定性合成基本面（离线测试用）。"""
    h = int(hashlib.md5(code.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(h)
    growth = float(rng.uniform(-0.3, 0.8))
    return {
        "code": code,
        "name": code,
        "period": "2026Q2",
        "netprofit_yoy": growth,
        "or_yoy": float(rng.uniform(-0.2, 0.6)),
        "roe": float(rng.uniform(-0.05, 0.25)),
        "pe_ttm": float(rng.uniform(5, 80)),
        "pe_pct": float(rng.uniform(0, 1)),
        "pb": float(rng.uniform(0.5, 10)),
    }


def _ts_client(config: Config):
    """惰性 tushare 客户端（带端点覆盖）。"""
    import tushare as ts  # noqa: PLC0415

    pro = ts.pro_api(config.data.tushare_token)
    if config.data.tushare_endpoint:
        try:
            pro._DataApi__http_url = config.data.tushare_endpoint  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return pro


def fetch_fundamental(config: Config, code: str, source: str,
                      start: str = "", end: str = "") -> dict:
    """个股基本面：净利润/营收同比、ROE、PE-TTM/PB 及 PE 历史分位。"""
    if source == "synthetic":
        return _synthetic_fundamental(code)

    try:
        pro = _ts_client(config)
        fin = pro.fina_indicator(ts_code=code, limit=1)
        if fin is None or fin.empty:
            return {}
        r = fin.iloc[0]
        out = {
            "code": code,
            "name": code,
            "period": str(r.get("end_date", "")),
            "netprofit_yoy": float(r["netprofit_yoy"]) if r.get("netprofit_yoy") is not None else None,
            "or_yoy": float(r["or_yoy"]) if r.get("or_yoy") is not None else None,
            "roe": float(r["roe"]) if r.get("roe") is not None else None,
        }
        if end and start:
            db = pro.daily_basic(ts_code=code, start_date=start, end_date=end)
            if db is not None and not db.empty:
                pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
                pe_pos = pe[pe > 0]
                r2 = db.iloc[-1]
                out["pe_ttm"] = float(r2["pe_ttm"]) if r2.get("pe_ttm") is not None else None
                out["pb"] = float(r2["pb"]) if r2.get("pb") is not None else None
                out["pe_pct"] = float((pe_pos < out["pe_ttm"]).mean()) if len(pe_pos) > 20 else None
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] 基本面获取失败：%s", code, e)
        return {}


def detect_disclosures(config: Config, date: str, source: str,
                       members: Optional[List[str]] = None) -> List[dict]:
    """当日新披露检测（ann_date 口径）。members 给定时只返回属于该集合的披露。"""
    if source == "synthetic":
        # 合成：按 code 哈希伪随机挑 3% 的成员当日"披露"
        universe = members or [f"S{i:04d}" for i in range(60)]
        out = []
        for c in universe:
            h = int(hashlib.md5(f"{c}|{date}".encode()).hexdigest()[:8], 16)
            if h % 100 < 3:
                out.append({"code": c, "name": c, "kind": "预告", "period": "2026Q2"})
        return out

    found: List[dict] = []
    try:
        pro = _ts_client(config)
        for kind, call in (("预告", lambda: pro.forecast(ann_date=date)),
                           ("快报", lambda: pro.express(ann_date=date))):
            try:
                df = call()
            except Exception as e:  # noqa: BLE001
                log.warning("披露检测 %s(%s) 失败：%s", date, kind, e)
                continue
            if df is None or df.empty:
                continue
            for r in df.itertuples():
                code = str(r.ts_code)
                if members and code not in members:
                    continue
                found.append({"code": code, "name": code, "kind": kind,
                              "period": str(r.end_date)})
    except Exception as e:  # noqa: BLE001
        log.warning("披露检测失败：%s", e)
    return found


def sector_followup(members: List[str], config: Config, source: str, end: str,
                    start: str = "") -> dict:
    """龙头板块财报跟进（F3.2）：新披露成员景气汇总 → 主线强化/退潮预警。"""
    disc = detect_disclosures(config, end, source, members)
    rows = []
    for d in disc:
        f = fetch_fundamental(config, d["code"], source, start, end)
        if not f:
            continue
        f["kind"] = d["kind"]
        rows.append(f)
    if not rows:
        return {"disclosures": [], "beat_ratio": None, "verdict": "无新披露", "rows": []}
    growths = [r["netprofit_yoy"] for r in rows if r.get("netprofit_yoy") is not None]
    beat = [g for g in growths if g and g > 0.20]
    beat_ratio = len(beat) / len(growths) if growths else None
    if beat_ratio is not None and beat_ratio >= 0.5:
        verdict = "主线强化（超预期占比高）"
    elif any(g for g in growths if g is not None and g < -0.3):
        verdict = "退潮预警（存在暴雷）"
    else:
        verdict = "中性"
    return {"disclosures": disc, "beat_ratio": beat_ratio, "verdict": verdict, "rows": rows}


def market_scan(config: Config, source: str, end: str, start: str = "",
                universe_limit: int = 50, universe: Optional[List[str]] = None) -> List[dict]:
    """全市场财报扫描（F3.3）：universe 内个股基本面 → 四象限。

    universe 给定时按给定股票池扫描（与板块聚合对齐）；否则取 stock_basic 前 N 只。
    """
    if source == "synthetic":
        codes = [f"S{i:04d}" for i in range(universe_limit)]
    elif universe:
        codes = list(dict.fromkeys(universe))[:universe_limit]
    else:
        try:
            pro = _ts_client(config)
            sb = pro.stock_basic(exchange="", list_status="L", limit=universe_limit)
            codes = list(sb["ts_code"]) if sb is not None and not sb.empty else []
        except Exception as e:  # noqa: BLE001
            log.warning("stock_basic 获取失败：%s", e)
            codes = []
    out = []
    for c in codes:
        f = fetch_fundamental(config, c, source, start, end)
        if not f:
            continue
        f["quadrant"] = quadrant(f.get("netprofit_yoy"), f.get("pe_pct"))
        out.append(f)
    return out


def post_reaction(price_df: pd.DataFrame, disclosure_date: str) -> dict:
    """财报后 1/3/5 日涨跌幅（按交易日顺延；无未来函数）。"""
    if price_df is None or len(price_df) < 6:
        return {"1d": None, "3d": None, "5d": None}
    idx = price_df.index
    target = pd.Timestamp(disclosure_date)
    pos = idx.searchsorted(target)  # 第一个 ≥ 披露日的交易日
    closes = price_df["close"].to_numpy()
    out = {}
    for label, n in (("1d", 1), ("3d", 3), ("5d", 5)):
        if pos + n < len(closes) and closes[pos] > 0:
            out[label] = float(closes[pos + n] / closes[pos] - 1.0)
        else:
            out[label] = None
    return out


def run_financial_analysis(config: Config, source: str = "auto", end: str = "",
                           start: str = "", universe_limit: int = 50,
                           board_members: Optional[Dict[str, List[str]]] = None,
                           universe: Optional[List[str]] = None) -> dict:
    """财报分析编排：全市场扫描 + 板块景气聚合 + 四象限 + 披露检测。"""
    if source == "auto":
        source = "synthetic" if not config.data.tushare_token else "tushare"
    end = (end or dt.date.today().strftime("%Y%m%d")).replace("-", "")
    from .data import default_start

    start = start or config.data.start or default_start(end)

    scan = market_scan(config, source, end, start, universe_limit, universe)
    for f in scan:
        f["quadrant"] = quadrant(f.get("netprofit_yoy"), f.get("pe_pct"))

    # 板块景气聚合（board_members: {board_name: [codes]}）
    sector_health: List[dict] = []
    if board_members:
        code2fund = {f["code"]: f for f in scan}

        def _match(c: str):
            if c in code2fund:
                return c
            base = str(c).split(".")[0]
            for k in code2fund:
                if str(k).split(".")[0] == base:
                    return k
            return None

        for bname, codes in board_members.items():
            matched = [code2fund[m] for m in map(_match, codes) if m]
            if not matched:
                continue
            growths = [m["netprofit_yoy"] for m in matched if m.get("netprofit_yoy") is not None]
            sector_health.append({
                "board": bname,
                "n": len(matched),
                "avg_growth": float(np.mean(growths)) if growths else None,
                "beat_ratio": (float(np.mean([1.0 if (g or 0) > 0.2 else 0.0 for g in growths]))
                               if growths else None),
            })

    disclosures = detect_disclosures(config, end, source)
    ordered = sorted(scan, key=lambda f: QUADRANT_ORDER.get(f.get("quadrant", "回避"), 9))
    return {"date": end, "source": source, "scan": ordered,
            "sector_health": sector_health, "disclosures": disclosures}
