"""IPO 估值预判（P4 / F4.x / §5.8）。

- 可比公司相对估值：同行业已上市 PE/PB/PS 中位数 × 发行后 EPS/BPS/每股营收 → 估值区间
- 发行定价判断：发行 PE vs 同行业可比中位数 vs 行业均值 → 折价/溢价（注册制市场化定价）
- DCF 校核：CAGR/毛利率/现金流 → 粗算（假设默认值表）
- 质量打分：盈利持续性/客户供应商集中度/关联交易/募投合理性 + 港股基石/保荐人
- IPO 专刊（Markdown）

数据：tushare `new_share` 主源；synthetic 离线；招股书 LLM 提取需 API key（未配时优雅降级）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .config import Config

log = logging.getLogger("market_monitor.ipo")


@dataclass
class IPOAnalysis:
    code: str
    name: str
    market: str                     # A | 港
    industry: str
    issue_price: Optional[float]
    issue_pe: Optional[float]
    issue_amount: Optional[float]   # 募资额（亿元）
    val_low: Optional[float]
    val_high: Optional[float]
    pricing: str                    # 折价 | 溢价 | 合理 | 无法判断
    dcf_value: Optional[float]
    quality_score: Optional[float]
    rating_short: str               # 打新评级：积极/谨慎/回避
    rating_long: str                # 长期价值：优选/一般/规避
    risk_factors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def comparable_valuation(pe_med: Optional[float], eps: Optional[float],
                         pb_med: Optional[float], bps: Optional[float],
                         ps_med: Optional[float], sps: Optional[float],
                         band: float = 0.15) -> Optional[tuple]:
    """可比估值区间：各可比中位数 × 发行后每股指标，取 [1−band, 1+band] 包络。"""
    est = []
    if pe_med and eps and pe_med > 0:
        est.append(pe_med * eps)
    if pb_med and bps and pb_med > 0:
        est.append(pb_med * bps)
    if ps_med and sps and ps_med > 0:
        est.append(ps_med * sps)
    if not est:
        return None
    mid = float(np.median(est))
    return mid * (1 - band), mid * (1 + band)


def pricing_judgment(issue_pe: Optional[float], comp_median: Optional[float],
                     industry_avg: Optional[float] = None, tol: float = 0.10) -> str:
    """发行定价判断（注册制市场化定价，§5.8）。"""
    if issue_pe is None or issue_pe <= 0:
        return "无法判断"
    base = comp_median if comp_median else industry_avg
    if not base or base <= 0:
        return "无法判断"
    diff = issue_pe / base - 1.0
    if diff > tol:
        return "溢价"
    if diff < -tol:
        return "折价"
    return "合理"


def dcf_rough(net_profit: Optional[float], cagr: Optional[float], years: int = 5,
              discount: float = 0.09, terminal_growth: float = 0.025,
              shares: Optional[float] = None) -> Optional[float]:
    """DCF 粗算：净利润 × (1+CAGR)^t 折现 + 永续终值；返回每股价值（需 shares）。"""
    if net_profit is None or cagr is None or not shares:
        return None
    if cagr <= terminal_growth or cagr <= -0.9:
        return None
    flows = [net_profit * (1 + cagr) ** t for t in range(1, years + 1)]
    pv = sum(f / (1 + discount) ** t for t, f in enumerate(flows, start=1))
    terminal = flows[-1] * (1 + terminal_growth) / (discount - terminal_growth)
    pv_terminal = terminal / (1 + discount) ** years
    return (pv + pv_terminal) / shares


def quality_score(profit_sustainability: float = 0.5, customer_risk: float = 0.5,
                  related_party: float = 0.5, fund_use: float = 0.5,
                  cornerstone: Optional[float] = None, sponsor: Optional[float] = None) -> float:
    """质量打分 0–100：盈利持续性/客户集中度/关联交易/募投合理性 各 25 分；港股加基石/保荐人。"""
    base = 25.0 * (profit_sustainability + (1 - customer_risk) + (1 - related_party) + fund_use)
    if cornerstone is not None and sponsor is not None:
        base = 20.0 * (profit_sustainability + (1 - customer_risk) + (1 - related_party) + fund_use) \
            + 10.0 * cornerstone + 10.0 * sponsor
    return float(np.clip(base, 0, 100))


def _rating(quality: float, pricing: str) -> tuple:
    """打新评级 + 长期价值评级。"""
    q = "积极" if quality >= 70 else ("谨慎" if quality >= 50 else "回避")
    p = "积极" if quality >= 75 and pricing != "溢价" else (q if quality >= 55 else "回避")
    long_r = "优选" if quality >= 75 else ("一般" if quality >= 55 else "规避")
    return p, long_r


def _synthetic_ipo(i: int) -> dict:
    h = int(hashlib.md5(f"ipo{i}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(h)
    issue_price = round(rng.uniform(5, 60), 2)
    pe = round(rng.uniform(12, 60), 2)
    return {
        "code": f"IPO{i:03d}", "name": f"合成新股{i + 1}", "market": "A",
        "industry": rng.choice(["医药", "半导体", "新能源", "消费", "软件"]),
        "issue_price": issue_price, "issue_pe": pe,
        "issue_amount": round(rng.uniform(3, 40), 2),
        "comp_pe": round(rng.uniform(15, 70), 2), "comp_pb": round(rng.uniform(1.5, 8), 2),
        "comp_ps": round(rng.uniform(2, 15), 2),
        "eps": round(rng.uniform(0.2, 2.5), 3), "bps": round(rng.uniform(2, 15), 2),
        "sps": round(rng.uniform(1, 12), 2),
        "net_profit": round(rng.uniform(0.5, 15), 2), "cagr": round(rng.uniform(0.05, 0.4), 3),
        "shares": round(rng.uniform(0.5, 5), 2),
        "q_profit": rng.uniform(0.3, 1), "q_customer": rng.uniform(0.1, 0.8),
        "q_related": rng.uniform(0.05, 0.6), "q_fund": rng.uniform(0.4, 1),
    }


def run_ipo_analysis(config: Config, source: str = "auto", end: str = "",
                     start: str = "", limit: int = 10) -> dict:
    """IPO 分析编排。source: tushare（new_share 真实数据）/ synthetic（离线）。"""
    if source == "auto":
        source = "synthetic" if not config.data.tushare_token else "tushare"
    end = (end or dt.date.today().strftime("%Y%m%d")).replace("-", "")

    rows: List[dict] = []
    if source == "synthetic":
        rows = [_synthetic_ipo(i) for i in range(limit)]
    else:
        try:
            import tushare as ts  # noqa: PLC0415

            pro = ts.pro_api(config.data.tushare_token)
            if config.data.tushare_endpoint:
                try:
                    pro._DataApi__http_url = config.data.tushare_endpoint  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            df = pro.new_share(start_date=start or "20260601", end_date=end)
            if df is not None and not df.empty:
                for r in df.head(limit).itertuples():
                    # new_share 实际列：price=发行价, pe=发行PE, funds=募资额(亿)
                    rows.append({
                        "code": str(r.ts_code), "name": str(r.name), "market": "A",
                        "industry": str(getattr(r, "industry", "") or ""),
                        "issue_price": _f(getattr(r, "price", None)),
                        "issue_pe": _f(getattr(r, "pe", None)),
                        "issue_amount": _f(getattr(r, "funds", None)),
                    })
        except Exception as e:  # noqa: BLE001
            log.warning("new_share 获取失败（%s），无 IPO 数据", e)

    analyses: List[IPOAnalysis] = []
    for r in rows:
        # 可比中位数：真实数据未提供时用行业默认（simplify: 需要时按 industry 拉可比公司）
        comp_pe = r.get("comp_pe")
        comp_pb = r.get("comp_pb")
        comp_ps = r.get("comp_ps")
        eps, bps, sps = r.get("eps"), r.get("bps"), r.get("sps")
        band = comparable_valuation(comp_pe, eps, comp_pb, bps, comp_ps, sps)
        val_low, val_high = (band[0], band[1]) if band else (None, None)
        pricing = pricing_judgment(r.get("issue_pe"), comp_pe)
        dcf = dcf_rough(r.get("net_profit"), r.get("cagr"), shares=r.get("shares"))
        has_q = any(k in r for k in ("q_profit", "q_customer", "q_related", "q_fund"))
        if has_q:
            q = quality_score(r.get("q_profit", 0.5), r.get("q_customer", 0.5),
                              r.get("q_related", 0.5), r.get("q_fund", 0.5))
            short_r, long_r = _rating(q, pricing)
            risks = []
            if r.get("q_customer", 0) > 0.6:
                risks.append("客户集中度过高")
            if r.get("q_related", 0) > 0.4:
                risks.append("关联交易占比较高")
            if pricing == "溢价":
                risks.append("发行定价偏高")
            if not risks:
                risks.append("盈利持续性待跟踪")
        else:
            # 真实数据缺少招股书质量输入（F4.3 需 LLM 解析）：不做虚假打分
            q = None
            short_r = "谨慎" if pricing != "溢价" else "回避"
            long_r = "一般"
            risks = ["招股书质量数据待 LLM 解析（F4.3）"]
            if pricing == "溢价":
                risks.append("发行定价偏高")
        analyses.append(IPOAnalysis(
            code=r["code"], name=r["name"], market=r["market"], industry=r.get("industry", ""),
            issue_price=r.get("issue_price"), issue_pe=r.get("issue_pe"),
            issue_amount=r.get("issue_amount"),
            val_low=val_low, val_high=val_high, pricing=pricing, dcf_value=dcf,
            quality_score=q, rating_short=short_r, rating_long=long_r, risk_factors=risks,
        ))
    return {"date": end, "source": source, "ipos": analyses}


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
