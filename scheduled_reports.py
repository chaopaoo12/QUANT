"""GitHub Actions 定时任务：生成报告并按报告类型各发一封邮件。

任务：
  daily — 大盘日报 + 板块日报 + 龙头板块财报跟进（当日有新披露才发），每个报告单独一封
  ipo   — IPO 估值预判（周末执行）

用法：
  python scheduled_reports.py --task daily [--end YYYYMMDD] [--sector-boards 20]
  python scheduled_reports.py --task ipo   [--end YYYYMMDD]

SMTP 凭证从环境变量读取（GitHub Secrets 注入）：
  SMTP_SERVER / SMTP_PORT / SMTP_FROM / SMTP_TO / SMTP_PASSWORD
未配置 SMTP 时只生成报告不发送（便于本地调试）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

from market_monitor.config import load_config
from market_monitor.mailer import mail_config, send_report_email

log = logging.getLogger("scheduled_reports")


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


def task_daily(config, end: str, sector_boards: int) -> None:
    from market_monitor.cli import run
    from market_monitor.report import (render_leader_followup, render_sector_daily,
                                       render_daily_report)
    from market_monitor.sectors import run_sector_analysis

    # 1) 大盘日报
    print("=== 大盘日报 ===")
    md, _ = run(config, end, "daily", None)
    _write_and_send(config, end, "daily", "大盘日报", md)

    # 2) 板块日报 + 3) 龙头板块财报跟进（共用一次板块分析）
    print("=== 板块日报 + 龙头财报跟进 ===")
    sec = run_sector_analysis(config, source="auto", end=end, limit=sector_boards)
    md = render_sector_daily(sec, config.sector.top_n)
    _write_and_send(config, end, "sector", "板块日报", md)

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
    parser.add_argument("--task", choices=["daily", "ipo"], required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--end", default=None, help="目标日期 YYYYMMDD（默认今天）")
    parser.add_argument("--sector-boards", type=int, default=20,
                        help="板块日报/龙头财报用板块数（控制运行时长，默认 20）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    end = (args.end or dt.date.today().strftime("%Y%m%d")).replace("-", "")

    if args.task == "daily":
        task_daily(config, end, args.sector_boards)
    else:
        task_ipo(config, end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
