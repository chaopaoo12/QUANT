"""GitHub Actions 定时任务：生成报告并按报告类型各发一封邮件。

任务：
  daily          — 大盘分类日报（指数/海外知名企业/数字货币/大宗商品期货/汇率，各一封）
  sector-financial — 板块日报 + 财报分析 + 龙头板块财报跟进（当日有新披露才发），各一封（18:00 执行）
  ipo            — IPO 估值预判（周末执行）

用法：
  python scheduled_reports.py --task daily [--end YYYYMMDD]
  python scheduled_reports.py --task sector-financial [--end YYYYMMDD] [--sector-boards 20] [--fin-universe 30]
  python scheduled_reports.py --task ipo [--end YYYYMMDD]

SMTP 凭证从环境变量读取（GitHub Secrets 注入）：
  SMTP_SERVER / SMTP_PORT / SMTP_FROM / SMTP_TO / SMTP_PASSWORD
未配置 SMTP 时只生成报告不发送（便于本地调试）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from market_monitor.config import load_config
from market_monitor.mailer import mail_config, send_report_email

log = logging.getLogger("scheduled_reports")

# 大盘分类 → 邮件标题（每类单独一封；group 为 config.symbols 的键）
DAILY_EMAIL_GROUPS = [
    ("大盘日报", ["indices_a", "indices_us", "indices_hk"]),
    ("海外知名企业日报", ["us_stocks"]),
    ("数字货币日报", ["crypto"]),
    ("大宗商品期货日报", ["futures", "futures_cn"]),
    ("汇率日报", ["fx"]),
]


def _write_and_send(config, end: str, name: str, title: str, md: str) -> bool:
    """写报告到 reports/YYYY-MM-DD/ 并发送邮件（每个报告单独一封）。"""
    out_dir = config.resolve_path(config.report.out_dir) / f"{end[:4]}-{end[4:6]}-{end[6:]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{name}.md"
    out_file.write_text(md, encoding="utf-8")
    print(f"[报告已生成] {out_file}")

    m = mail_config()
    if not m:
        print("[mail] 未配置 SMTP（SMTP_* 环境变量），跳过邮件发送")
        return False
    subject = f"{title} {end[:4]}-{end[4:6]}-{end[6:]}"
    ok = send_report_email(subject, md, **m)
    print(f"[邮件{'成功' if ok else '失败'}] {subject}")
    return ok


def _symbol_groups(config) -> dict:
    """group_name -> set(symbol)。"""
    return {g: {s["symbol"] for s in specs} for g, specs in config.symbols.items()}


def task_daily(config, end: str) -> None:
    """大盘分类日报：一次拉取全部标的，按分类各发一封邮件。"""
    from market_monitor.cli import run
    from market_monitor.report import render_daily_report

    print("=== 大盘分类日报（一次拉取，分类邮件） ===")
    _, results = run(config, end, "daily", None)
    groups = _symbol_groups(config)

    for title, keys in DAILY_EMAIL_GROUPS:
        syms = set()
        for k in keys:
            syms |= groups.get(k, set())
        subset = [r for r in results if r["symbol"] in syms]
        if not subset:
            print(f"[{title}] 无可用数据，跳过")
            continue
        md = render_daily_report(subset, end, title=title)
        _write_and_send(config, end, title.split("日报")[0], title, md)


def _leader_financial_followup(config, sector_result, end: str) -> dict:
    """进攻榜板块财报跟进（F3.2）：检查进攻榜板块成员的当日新披露。"""
    from market_monitor.financials import sector_followup

    source = "tushare" if config.data.tushare_token else "synthetic"
    sectors = {s.code: s for s in sector_result.get("sectors", [])}
    boards = []
    for a in sector_result["offensive"]:
        sec = sectors.get(a.code)
        if sec is None:
            continue
        f = sector_followup(sec.members, config, source, end)
        if f.get("disclosures"):
            f["name"] = a.name
            boards.append(f)
    return {"date": end, "boards": boards}


def task_sector_financial(config, end: str, sector_boards: int, fin_universe: int) -> None:
    """板块日报 + 财报分析 + 龙头板块财报跟进（每天 18:00）。"""
    from market_monitor.financials import run_financial_analysis
    from market_monitor.report import (render_financial_report, render_leader_followup,
                                       render_sector_daily)
    from market_monitor.sectors import run_sector_analysis

    # 1) 板块日报（A股概念板块，含龙头）
    print("=== 板块日报 ===")
    sec = run_sector_analysis(config, source="auto", end=end, limit=sector_boards)
    md = render_sector_daily(sec, config.sector.top_n)
    _write_and_send(config, end, "sector", "板块日报", md)

    # 2) 财报分析（景气-估值四象限 + 板块景气聚合）
    print("=== 财报分析 ===")
    source = "tushare" if config.data.tushare_token else "synthetic"
    board_members = {s.name: s.members for s in sec.get("sectors", [])}
    universe = [c for s in sec.get("sectors", []) for c in s.members]
    fin = run_financial_analysis(config, source=source, end=end,
                                 universe_limit=fin_universe,
                                 board_members=board_members, universe=universe)
    md = render_financial_report(fin)
    _write_and_send(config, end, "financial", "财报分析", md)

    # 3) 龙头板块财报跟进（进攻榜有新披露才发）
    print("=== 龙头板块财报跟进 ===")
    followup = _leader_financial_followup(config, sec, end)
    if followup["boards"]:
        md = render_leader_followup(followup, end)
        _write_and_send(config, end, "leader-financial", "龙头板块财报跟进", md)
    else:
        print("进攻榜板块当日无新披露，跳过财报跟进邮件")


def task_ipo(config, end: str) -> None:
    from market_monitor.ipo import run_ipo_analysis
    from market_monitor.report import render_ipo_report

    print("=== IPO 估值预判 ===")
    result = run_ipo_analysis(config, source="auto", end=end, limit=10)
    md = render_ipo_report(result)
    _write_and_send(config, end, "ipo", "IPO 估值预判", md)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Market Monitor 定时任务（生成报告 + 邮件）")
    parser.add_argument("--task", choices=["daily", "sector-financial", "ipo"], required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--end", default=None, help="目标日期 YYYYMMDD（默认今天）")
    parser.add_argument("--sector-boards", type=int, default=20,
                        help="板块数（板块/财报任务，控制运行时长，默认 20）")
    parser.add_argument("--fin-universe", type=int, default=30,
                        help="财报扫描股票数（默认 30）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    end = (args.end or dt.date.today().strftime("%Y%m%d")).replace("-", "")

    if args.task == "daily":
        task_daily(config, end)
    elif args.task == "sector-financial":
        task_sector_financial(config, end, args.sector_boards, args.fin_universe)
    else:
        task_ipo(config, end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
