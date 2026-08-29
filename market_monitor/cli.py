"""命令行入口（§7 调度 / 手动运行）：python main.py --end YYYYMMDD [--report daily|weekly]。"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import sys
from pathlib import Path
from typing import List, Optional

from .config import Config, load_config
from .data import DataManager, asof_date, default_start
from .regime import compute_regime
from .signals import generate_signals, volatility_state, detect_alerts
from .backtest import backtest_symbol, summarize_backtest
from .report import render_daily_report, render_weekly_report

log = logging.getLogger("market_monitor")


def _isna(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _build_result(symbol: str, name: str, source: str, df, config: Config) -> dict:
    enriched = compute_regime(df, config.regime, config.indicators)
    signals = generate_signals(enriched, config.regime, config.indicators, config.cost)
    trades, equity = backtest_symbol(enriched, signals, config.cost)
    bt = summarize_backtest(trades, equity)

    last = enriched.iloc[-1]
    asof = asof_date(enriched)

    signals_today = [s.to_dict() for s in signals if s.date == asof]
    last5 = set(enriched.index[-5:])
    signals_week = [s.to_dict() for s in signals if s.date in last5]

    close = float(last["close"])
    week_change = None
    bw_prev = None
    bw_change = None
    if len(enriched) >= 6:
        week_change = close / float(enriched["close"].iloc[-6]) - 1.0
        bw_prev = float(enriched["bw"].iloc[-6])
        if not _isna(bw_prev) and bw_prev != 0:
            bw_change = float(last["bw"]) / bw_prev - 1.0

    # 价格距 BOLL 上/中/下轨的距离（相对%，正=在轨上方，负=在轨下方）
    def _boll_pos(level):
        if _isna(level) or level == 0:
            return None
        return close / float(level) - 1.0

    return {
        "symbol": symbol,
        "name": name,
        "source": source,
        "asof_date": str(asof.date()) if asof is not None else None,
        "daily_state": last.get("state"),
        "weekly_state": last.get("weekly_state"),
        "vol_state": volatility_state(last.get("bw_pct")),
        "close": close,
        "boll_pos_upper": _boll_pos(last.get("boll_upper")),
        "boll_pos_mid": _boll_pos(last.get("boll_mid")),
        "boll_pos_lower": _boll_pos(last.get("boll_lower")),
        "signals_today": signals_today,
        "signals_week": signals_week,
        "alerts": detect_alerts(enriched),
        "week_change": week_change,
        "bw_now": float(last["bw"]) if not _isna(last["bw"]) else None,
        "bw_prev": bw_prev,
        "bw_change": bw_change,
        # 回测指标（净成本口径）
        "n_signals": len(signals),
        "trades": bt["trades"],
        "win_rate": bt["win_rate"],
        "profit_factor": bt["profit_factor"],
        "expectancy": bt["expectancy"],
        "max_drawdown": bt["max_drawdown"],
        "sharpe": bt["sharpe"],
        "by_state": bt["by_state"],
    }


def run(config: Config, end: str, report_type: str, source_override: Optional[str] = None) -> tuple[str, List[dict]]:
    """执行一次分析，返回 (markdown 文本, 逐标的 result 列表)。"""
    if source_override:
        config.data.source = source_override

    end = (end or dt.date.today().strftime("%Y%m%d")).replace("-", "")
    start = config.data.start or default_start(end)

    dm = DataManager(config)
    results: List[dict] = []

    for group, specs in config.symbols.items():
        for spec in specs:
            symbol = spec["symbol"]
            name = spec.get("name", symbol)
            src = source_override or spec.get("source", config.data.source)
            try:
                df = dm.get(symbol, src, start, end)
            except Exception as e:  # noqa: BLE001 —— 单标的失败不中断（F5.6）
                log.warning("[%s] 数据获取失败：%s", symbol, e)
                continue
            if df is None or len(df) < 60:
                log.warning("[%s] 数据不足（<60 根），跳过", symbol)
                continue
            try:
                results.append(_build_result(symbol, name, src, df, config))
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] 分析失败：%s", symbol, e)

    if report_type == "weekly":
        md = render_weekly_report(results, end)
    else:
        md = render_daily_report(results, end)
    return md, results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="全球金融市场监控系统（P0–P4）")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--end", default=None, help="目标日期 YYYYMMDD（默认今天）")
    parser.add_argument("--report", choices=["daily", "weekly", "sector", "sector-weekly", "financial", "ipo"], default="daily")
    parser.add_argument("--source", default=None,
                        choices=["auto", "tushare", "yfinance", "akshare", "synthetic"],
                        help="强制覆盖数据源（--source synthetic 可离线跑通全链路）")
    parser.add_argument("--sector-source", default=None,
                        choices=["auto", "tushare", "akshare", "synthetic"],
                        help="板块数据源（P1；默认 tushare，合成可用 synthetic）")
    parser.add_argument("--sector-limit", type=int, default=None,
                        help="板块数量上限（真实源演示建议 20–50，默认全量）")
    parser.add_argument("--fin-source", default=None,
                        choices=["auto", "tushare", "synthetic"],
                        help="财报数据源（P3；默认 synthetic 当无 token 时）")
    parser.add_argument("--fin-universe", type=int, default=50,
                        help="财报扫描股票数量（P3）")
    parser.add_argument("--fin-boards", type=int, default=None,
                        help="财报板块景气聚合使用的板块数（P3；需拉板块成员，0 关闭）")
    parser.add_argument("--ipo-source", default=None,
                        choices=["auto", "tushare", "synthetic"],
                        help="IPO 数据源（P4；默认 synthetic 当无 token 时）")
    parser.add_argument("--ipo-limit", type=int, default=10,
                        help="IPO 数量上限（P4）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 报告含 ⚠/emoji 等非 GBK 字符，控制台 print 需 UTF-8，否则 Windows 中文区域会 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    config = load_config(args.config)
    end = (args.end or dt.date.today().strftime("%Y%m%d")).replace("-", "")
    out_dir = config.resolve_path(config.report.out_dir) / f"{end[:4]}-{end[4:6]}-{end[6:]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.report == "ipo":
        from .ipo import run_ipo_analysis
        from .report import render_ipo_report

        ipo_src = args.ipo_source or ("synthetic" if not config.data.tushare_token else "tushare")
        result = run_ipo_analysis(config, source=ipo_src, end=end,
                                  start=config.data.start or "", limit=args.ipo_limit)
        md = render_ipo_report(result)
        out_file = out_dir / "ipo.md"
        out_file.write_text(md, encoding="utf-8")
        print(f"[完成] IPO 数={len(result['ipos'])}")
        print(f"[报告] {out_file}")
        print()
        print(md)
        return 0

    if args.report == "financial":
        from .financials import run_financial_analysis
        from .report import render_financial_report

        fin_src = args.fin_source or ("synthetic" if not config.data.tushare_token else "tushare")
        board_members = None
        universe = None
        if args.fin_boards:
            import re

            from .data import default_start
            from .data.sector_source import make_sector_store

            store = make_sector_store(config, "synthetic" if fin_src == "synthetic" else "tushare")
            # ths_daily 代理对近端 start 只返回约 20 行，须用 default_start 拉足历史（≥60 根）
            board_members = {}
            universe = []
            a_share = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
            for sec in store.list_sectors(default_start(end), end, limit=args.fin_boards):
                board_members[sec.name] = sec.members
                universe.extend(m for m in sec.members if a_share.match(m))
        result = run_financial_analysis(config, source=fin_src, end=end,
                                        universe_limit=args.fin_universe,
                                        board_members=board_members, universe=universe)
        md = render_financial_report(result)
        out_file = out_dir / "financial.md"
        out_file.write_text(md, encoding="utf-8")
        print(f"[完成] 扫描股票数={len(result['scan'])} 板块聚合={len(result.get('sector_health', []))}")
        print(f"[报告] {out_file}")
        print()
        print(md)
        return 0

    if args.report in ("sector", "sector-weekly"):
        from .sectors import run_sector_analysis
        from .report import render_sector_daily, render_sector_weekly

        src = args.sector_source or (args.source if args.source and args.source != "auto" else "auto")
        result = run_sector_analysis(config, source=src, end=end, limit=args.sector_limit)
        if args.report == "sector":
            md = render_sector_daily(result, config.sector.top_n)
        else:
            md = render_sector_weekly(result, config.sector.top_n)
        out_file = out_dir / f"{args.report}.md"
        out_file.write_text(md, encoding="utf-8")
        n = len(result["analyses"])
        print(f"[完成] 板块数={n}")
        print(f"[报告] {out_file}")
        print()
        print(md)
        return 0

    md, results = run(config, args.end, args.report, args.source)

    out_file = out_dir / f"{args.report}.md"
    out_file.write_text(md, encoding="utf-8")

    print(f"[完成] 标的数={len(results)}")
    print(f"[报告] {out_file}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
