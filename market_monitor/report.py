"""日报/周报 Markdown 生成（F1.9 / F1.10 / §6）。

报告按市场分组展示各自 asof_date；跨市场强弱排名仅对 asof_date 对齐的数据进行。
"""
from __future__ import annotations

import math
from typing import List

import pandas as pd

from .regime import STATE_LABELS

_ACTION_LABELS = {"buy": "买入", "add": "加仓", "bull_start": "突破启动(最高置信)"}


def _md_table(headers: List[str], rows: List[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        cells = ["—" if (v is None or v == "") else str(v) for v in r]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _label(state) -> str:
    return STATE_LABELS.get(state, str(state))


def _pct(x, nd=2) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.{nd}f}%"


def _num(x, nd=2) -> str:
    if x is None or pd.isna(x):
        return "—"
    try:
        if not math.isfinite(x):
            return "∞" if x > 0 else "-∞"
    except TypeError:
        return str(x)
    return f"{x:.{nd}f}"


def render_daily_report(results: List[dict], target_date: str, title: str = "大盘日报") -> str:
    """大盘日报。results 元素见 cli._build_result；title 用于分类报告（各分类单独出邮件）。"""
    date_str = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"

    lines = [f"# {title} {date_str}", ""]

    lines.append("## 市场状态与波动总览")
    lines.append(_md_table(
        ["标的", "代码", "数据源", "asof_date", "日线状态", "周线状态", "波动状态", "收盘",
         "距上轨", "距中轨", "距下轨"],
        [[r["name"], r["symbol"], r["source"], r.get("asof_date") or "无数据",
          _label(r.get("daily_state")), _label(r.get("weekly_state")),
          r.get("vol_state") or "—", _num(r.get("close")),
          _pct(r.get("boll_pos_upper")), _pct(r.get("boll_pos_mid")),
          _pct(r.get("boll_pos_lower"))] for r in results],
    ))
    lines.append("")

    lines.append("## 当日信号与入场计划")
    sig_rows = []
    for r in results:
        for s in r.get("signals_today", []):
            skip = "❌不出手(盈亏比<1.5)" if s.get("skipped") else ""
            sig_rows.append([
                r["name"], _ACTION_LABELS.get(s["action"], s["action"]), _label(s["state"]),
                "次日开盘", _num(s.get("target")), _num(s.get("stop")),
                _num(s.get("rr_estimate")), skip or s.get("reason", ""),
            ])
    if sig_rows:
        lines.append(_md_table(
            ["标的", "信号", "状态", "入场", "止盈参考", "止损", "盈亏比(估)", "备注"], sig_rows))
    else:
        lines.append("_本日无入场信号。_")
    lines.append("")

    lines.append("## 预警清单")
    alert_rows = []
    for r in results:
        for a in r.get("alerts", []):
            alert_rows.append([r["name"], a])
    if alert_rows:
        lines.append(_md_table(["标的", "预警"], alert_rows))
    else:
        lines.append("_无预警。_")
    lines.append("")

    lines.append("## 回测评估（含交易成本，净成本口径）")
    lines.append(_md_table(
        ["标的", "信号数", "交易数", "胜率", "盈亏因子", "期望值", "最大回撤", "夏普"],
        [[r["name"], r.get("n_signals", 0), r.get("trades", 0),
          _pct(r.get("win_rate")), _num(r.get("profit_factor")),
          _num(r.get("expectancy")), _pct(r.get("max_drawdown")), _num(r.get("sharpe"))]
         for r in results],
    ))
    lines.append("")
    lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
    return "\n".join(lines)


def render_sector_daily(result: dict, top_n: int = 5) -> str:
    """板块日报（F2.6 / §6）：进攻/防守榜 + 龙头股 + 板块信号。"""
    date_str = f"{result['date'][:4]}-{result['date'][4:6]}-{result['date'][6:]}"
    lines = [f"# 板块日报 {date_str}", ""]

    def _leader_cell(leaders):
        if not leaders:
            return "—"
        cells = []
        for l in leaders:
            name = l.get("name") if isinstance(l, dict) else l.name
            kind = l.get("kind") if isinstance(l, dict) else l.kind
            cells.append(f"{name}({kind})")
        return "、".join(cells)

    lines.append(f"## 进攻（主线）Top{top_n}")
    rows = [[a.name, _label(a.state), _pct(a.rs_20), _num(a.corr), _num(a.vol_ratio),
             _pct(a.boll_pos_upper), _pct(a.boll_pos_mid), _pct(a.boll_pos_lower),
             _num(a.offensive_score), _leader_cell(a.leaders)] for a in result["offensive"]]
    lines.append(_md_table(["板块", "日线状态", "RS20", "相关性", "量比", "距上轨", "距中轨", "距下轨",
                            "进攻分", "龙头"], rows) if rows else "_无数据。_")
    lines.append("")

    lines.append(f"## 防守 Top{top_n}")
    rows = [[a.name, _label(a.state), _pct(a.rs_20), _num(a.beta), _num(a.mf_proxy),
             _pct(a.boll_pos_upper), _pct(a.boll_pos_mid), _pct(a.boll_pos_lower),
             _num(a.defensive_score), _leader_cell(a.leaders)] for a in result["defensive"]]
    lines.append(_md_table(["板块", "日线状态", "RS20", "Beta", "资金流代理", "距上轨", "距中轨", "距下轨",
                            "防守分", "龙头"], rows) if rows else "_无数据。_")
    lines.append("")

    lines.append("## 板块信号（板块指数择时）")
    sig_rows = []
    seen = set()
    for a in result["offensive"] + result["defensive"]:
        for s in a.signals:
            key = (a.name, s.get("action"), str(s.get("date"))[:10])
            if key in seen:
                continue
            seen.add(key)
            sig_rows.append([a.name, s.get("action"), _label(s.get("state")),
                             str(s.get("date"))[:10], s.get("reason", "")])
    lines.append(_md_table(["板块", "信号", "状态", "日期", "原因"], sig_rows) if sig_rows else "_本日无板块信号。_")
    lines.append("")
    lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
    return "\n".join(lines)


def render_leader_followup(result: dict, target_date: str) -> str:
    """龙头板块财报跟进（F3.2）：进攻榜板块新披露 → 主线强化/退潮预警。"""
    date_str = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    lines = [f"# 龙头板块财报跟进 {date_str}", ""]
    boards = result.get("boards", [])
    if not boards:
        lines.append("_进攻榜板块当日无新财报披露，主线暂无业绩面变化。_")
        lines.append("")
        lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
        return "\n".join(lines)
    for b in boards:
        lines.append(f"## {b['name']}")
        lines.append(f"- 新披露：**{len(b['disclosures'])}** 家 | 超预期占比：**{_pct(b.get('beat_ratio'))}**"
                     f" | 结论：**{b.get('verdict', '中性')}**")
        rows = b.get("rows", [])
        if rows:
            lines.append(_md_table(
                ["代码", "名称", "类型", "净利同比", "营收同比", "ROE"],
                [[r["code"], r.get("name", r["code"]), r.get("kind", ""),
                  _pct(r.get("netprofit_yoy")), _pct(r.get("or_yoy")), _pct(r.get("roe"))]
                 for r in rows],
            ))
        lines.append("")
    lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
    return "\n".join(lines)


def render_ipo_report(result: dict) -> str:
    """IPO 专刊（P4 / F4.7 / §6）：合理估值区间 / 定价高低 / 打新评级 / 长期评级 / 核心风险。"""
    date_str = f"{result['date'][:4]}-{result['date'][4:6]}-{result['date'][6:]}"
    lines = [f"# IPO 估值预判 {date_str}（数据源：{result['source']}）", ""]
    ipos = result.get("ipos", [])
    if ipos:
        lines.append(_md_table(
            ["名称", "市场", "行业", "发行价", "发行PE", "合理估值区间", "定价判断",
             "DCF(元/股)", "质量分", "打新评级", "长期评级", "核心风险"],
            [[i.name, i.market, i.industry, _num(i.issue_price), _num(i.issue_pe),
              (f"{_num(i.val_low)}–{_num(i.val_high)}" if i.val_low is not None else "—"),
              i.pricing, _num(i.dcf_value), _num(i.quality_score, 0),
              i.rating_short, i.rating_long, "；".join(i.risk_factors)]
             for i in ipos],
        ))
    else:
        lines.append("_无 IPO 数据（检查数据源与时间窗口）。_")
    lines.append("")
    lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
    return "\n".join(lines)


def render_financial_report(result: dict) -> str:
    """财报专刊/扫描（P3 / §6）：景气-估值四象限 + 板块景气聚合 + 当日披露。"""
    date_str = f"{result['date'][:4]}-{result['date'][4:6]}-{result['date'][6:]}"
    lines = [f"# 财报分析 {date_str}（数据源：{result['source']}）", ""]

    lines.append("## 景气-估值四象限")
    scan = result.get("scan", [])
    if scan:
        lines.append(_md_table(
            ["代码", "名称", "期间", "净利同比", "营收同比", "ROE", "PE-TTM", "PE分位", "象限"],
            [[r["code"], r.get("name", r["code"]), r.get("period", ""),
              _pct(r.get("netprofit_yoy")), _pct(r.get("or_yoy")), _pct(r.get("roe")),
              _num(r.get("pe_ttm")), _pct(r.get("pe_pct")), r.get("quadrant", "—")]
             for r in scan],
        ))
    else:
        lines.append("_无数据。_")
    lines.append("")

    lines.append("## 板块景气度聚合（按概念板块成份）")
    health = result.get("sector_health", [])
    if health:
        lines.append(_md_table(
            ["板块", "成份数", "平均净利同比", "超预期占比"],
            [[h["board"], h["n"], _pct(h.get("avg_growth")), _pct(h.get("beat_ratio"))]
             for h in health],
        ))
    else:
        lines.append("_未提供板块聚合（可加 --fin-boards N 启用）。_")
    lines.append("")

    lines.append("## 当日披露")
    disc = result.get("disclosures", [])
    if disc:
        lines.append(_md_table(["代码", "类型", "期间"],
                               [[d["code"], d["kind"], d.get("period", "")] for d in disc]))
    else:
        lines.append("_当日无新披露。_")
    lines.append("")
    lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
    return "\n".join(lines)


def render_sector_weekly(result: dict, top_n: int = 5) -> str:
    """板块周报：板块强弱排名与轮动（F2.6 / §6 周报栏目）。"""
    date_str = f"{result['date'][:4]}-{result['date'][4:6]}-{result['date'][6:]}"
    lines = [f"# 板块周报 {date_str}", ""]

    ranked = sorted([a for a in result["analyses"] if a.rs_20 is not None],
                    key=lambda a: a.rs_20, reverse=True)
    lines.append("## 板块强弱排名（按 RS20，相对上证）")
    if ranked:
        lines.append(_md_table(
            ["排名", "板块", "日线状态", "周线状态", "RS20", "RS60", "相关性", "进攻分", "防守分"],
            [[i + 1, a.name, _label(a.state), _label(a.weekly_state), _pct(a.rs_20),
              _pct(a.rs_60), _num(a.corr), _num(a.offensive_score), _num(a.defensive_score)]
             for i, a in enumerate(ranked)],
        ))
    else:
        lines.append("_无数据。_")
    lines.append("")
    lines.append(f"## 进攻/防守榜（Top{top_n}）")
    off = [a.name for a in result["offensive"]]
    deff = [a.name for a in result["defensive"]]
    lines.append(f"- **进攻主线**：{'、'.join(off) if off else '—'}")
    lines.append(f"- **防守观察**：{'、'.join(deff) if deff else '—'}")
    lines.append("")
    lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
    return "\n".join(lines)


def render_weekly_report(results: List[dict], target_date: str) -> str:
    date_str = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    lines = [f"# 周报 {date_str}", ""]

    lines.append("## 周线趋势全景")
    lines.append(_md_table(
        ["标的", "代码", "asof_date", "周线状态", "周涨跌", "带宽(本周)", "带宽(上周)", "带宽变化",
         "距上轨", "距中轨", "距下轨"],
        [[r["name"], r["symbol"], r.get("asof_date") or "无数据", _label(r.get("weekly_state")),
          _pct(r.get("week_change")), _num(r.get("bw_now")), _num(r.get("bw_prev")),
          _pct(r.get("bw_change")), _pct(r.get("boll_pos_upper")), _pct(r.get("boll_pos_mid")),
          _pct(r.get("boll_pos_lower"))] for r in results],
    ))
    lines.append("")

    # 市场强弱排名（周涨跌，asof_date 对齐）
    ranked = sorted([r for r in results if r.get("week_change") is not None],
                    key=lambda r: r["week_change"], reverse=True)
    lines.append("## 市场强弱排名（按周涨跌，asof_date 对齐）")
    if ranked:
        lines.append(_md_table(
            ["排名", "标的", "周涨跌", "asof_date"],
            [[i + 1, r["name"], _pct(r["week_change"]), r.get("asof_date") or "—"]
             for i, r in enumerate(ranked)],
        ))
    else:
        lines.append("_无数据。_")
    lines.append("")

    lines.append("## 本周信号回顾")
    sig_rows = []
    for r in results:
        for s in r.get("signals_week", []):
            sig_rows.append([r["name"], _ACTION_LABELS.get(s["action"], s["action"]),
                             _label(s["state"]), s.get("date"), s.get("reason", "")])
    if sig_rows:
        lines.append(_md_table(["标的", "信号", "状态", "日期", "原因"], sig_rows))
    else:
        lines.append("_本周无信号。_")
    lines.append("")

    lines.append("## 预警清单")
    alert_rows = [[r["name"], a] for r in results for a in r.get("alerts", [])]
    lines.append(_md_table(["标的", "预警"], alert_rows) if alert_rows else "_无预警。_")
    lines.append("")
    lines.append("> 免责声明：本报告仅供个人研究参考，不构成投资建议。")
    return "\n".join(lines)
