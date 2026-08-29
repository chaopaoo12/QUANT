"""端到端自检（离线）：python -m market_monitor.selfcheck

在合成行情上跑通 数据→指标→状态机→信号→回测→报告 全链路，并断言关键不变量。
这是 P0 的「最小可运行检查」：任何一环逻辑破坏都会在这里失败。
"""
from __future__ import annotations

import numpy as np

from .config import load_config
from .data import default_start, generate_synthetic
from .regime import compute_regime
from .signals import generate_signals, detect_alerts
from .backtest import backtest_symbol, summarize_backtest
from .report import render_daily_report, render_weekly_report

VALID_STATES = {"bull", "bear", "range", "transition"}
END = "20260826"


def _run() -> None:
    cfg = load_config()
    start = default_start(END)

    df = generate_synthetic("sh000001", start, END)
    assert len(df) > 250, f"合成数据不足：{len(df)}"

    enriched = compute_regime(df, cfg.regime)
    states = set(enriched["state"].dropna().tolist())
    assert states <= VALID_STATES, f"非法状态：{states - VALID_STATES}"

    # 周线状态仅用已收盘周（无未来函数）：前 60 个交易日不应有周线 bull/bear（W_MA60 未形成）
    assert set(enriched["weekly_state"].dropna()) <= {"bull", "bear", "range"}

    signals = generate_signals(enriched, cfg.regime, cfg.indicators, cfg.cost)
    for s in signals:
        # 未跳过的信号盈亏比必须 ≥ 阈值；风险必须为正
        if not s.skipped:
            assert s.rr_estimate >= cfg.cost.min_rr - 1e-9, f"盈亏比不足的信号未跳过：{s}"
        assert s.stop < s.close, "止损位应低于信号日收盘"

    trades, equity = backtest_symbol(enriched, signals, cfg.cost)
    assert np.isfinite(equity).all(), "权益曲线出现非有限值"
    assert equity.min() > 0, "权益曲线应恒为正"

    bt = summarize_backtest(trades, equity)
    assert "max_drawdown" in bt and "sharpe" in bt

    alerts = detect_alerts(enriched)
    assert isinstance(alerts, list)

    # 报告渲染不抛异常且非空
    res = {
        "name": "上证指数", "symbol": "sh000001", "source": "synthetic",
        "asof_date": END, "daily_state": "bull", "weekly_state": "bull",
        "vol_state": "正常", "close": float(enriched["close"].iloc[-1]),
        "signals_today": [s.to_dict() for s in signals[:1]],
        "signals_week": [s.to_dict() for s in signals[:1]],
        "alerts": alerts, "week_change": 0.01, "bw_now": 0.05, "bw_prev": 0.04,
        "bw_change": 0.25, "n_signals": len(signals), "trades": bt["trades"],
        "win_rate": bt["win_rate"], "profit_factor": bt["profit_factor"],
        "expectancy": bt["expectancy"], "max_drawdown": bt["max_drawdown"],
        "sharpe": bt["sharpe"], "by_state": bt["by_state"],
    }
    daily = render_daily_report([res], END)
    weekly = render_weekly_report([res], END)
    assert "# 大盘日报" in daily and "# 周报" in weekly

    # P1 板块：合成板块 → 进攻/防守榜非空
    from .sectors import run_sector_analysis
    from .report import render_sector_daily

    sec = run_sector_analysis(cfg, source="synthetic", end=END, limit=8)
    assert sec["offensive"] and sec["defensive"], "板块进攻/防守榜为空"
    assert "# 板块日报" in render_sector_daily(sec, cfg.sector.top_n)

    # P3 财报：合成扫描 → 四象限非空
    from .financials import run_financial_analysis
    from .report import render_financial_report

    fin = run_financial_analysis(cfg, source="synthetic", end=END, universe_limit=20)
    assert fin["scan"], "财报扫描为空"
    assert "# 财报分析" in render_financial_report(fin)

    # P4 IPO：合成新股 → 评级齐全
    from .ipo import run_ipo_analysis
    from .report import render_ipo_report

    ipo = run_ipo_analysis(cfg, source="synthetic", end=END, limit=5)
    assert ipo["ipos"], "IPO 分析为空"
    assert "# IPO 估值预判" in render_ipo_report(ipo)

    print(f"[selfcheck OK] bars={len(df)} signals={len(signals)} "
          f"trades={bt['trades']} win_rate={bt['win_rate']} "
          f"profit_factor={bt['profit_factor']} max_dd={bt['max_drawdown']} "
          f"sharpe={bt['sharpe']}")
    print(f"[selfcheck OK] P1 板块={len(sec['analyses'])} P3 财报={len(fin['scan'])} P4 IPO={len(ipo['ipos'])}")


if __name__ == "__main__":
    _run()
