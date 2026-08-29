"""回测与评估（F1.8 / §5.9）。

执行口径：
    - 信号次日持仓（shift(1)）：信号在 T 日收盘确认，T+1 开盘成交；
    - 交易成本模型：佣金（买卖）+ 印花税（卖出）+ 滑点（双边）；
    - 牛市：2×ATR 吊灯止损 trailing（不设上轨止盈，让利润奔跑）；
    - 震荡市：固定止损=下轨、止盈参考=上轨（触上轨后回落轨内止盈）。

简化说明（simplify:）：
    - 离场成交价用当日收盘（非次日开盘），无未来函数（只用 ≤t 收盘数据）；
    - 「跌破 MA10 减半仓 / 跌破中轨减仓」只记录为信号，P0 回测按整仓退出模拟；
      如需精细仓位，后续在 position 增加 qty 分级。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List

import numpy as np
import pandas as pd

from .config import CostModel
from .signals import SignalRecord


@dataclass
class Trade:
    entry_date: object
    exit_date: object
    state: str
    entry: float
    exit: float
    pnl: float
    ret: float
    reason: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_date"] = str(self.entry_date)
        d["exit_date"] = str(self.exit_date)
        return d


def _entry_cost_rate(cost: CostModel) -> float:
    return cost.commission_rate + cost.slippage_bp / 10000.0


def _exit_cost_rate(cost: CostModel) -> float:
    return cost.commission_rate + cost.stamp_tax_rate + cost.slippage_bp / 10000.0


def backtest_symbol(df: pd.DataFrame, signals: List[SignalRecord],
                    cost: CostModel | None = None) -> tuple[List[Trade], np.ndarray]:
    """对单标的做多头回测，返回 (trades, daily_equity_curve)。"""
    cost = cost or CostModel()
    entry_cost = _entry_cost_rate(cost)
    exit_cost = _exit_cost_rate(cost)

    sig_map: Dict[object, SignalRecord] = {s.date: s for s in signals if not s.skipped}

    opens = df["open"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    atrs = df["atr"].to_numpy(float)
    uppers = df["boll_upper"].to_numpy(float)
    lowers = df["boll_lower"].to_numpy(float)
    dates = list(df.index)
    n = len(df)

    trades: List[Trade] = []
    cash = 1.0
    shares = 0.0
    pos = None  # dict(entry, stop, target, state, highest_high)
    equity_curve = np.empty(n)

    for i in range(n):
        # 0) 当日市值（用 ≤i 数据：当前仓位在今日收盘的估值；新开仓在下方才发生，不影响今日）
        equity_curve[i] = cash + shares * closes[i]

        # 1) 管理已有仓位（用第 i 根 K 线）
        if pos is not None:
            pos["highest_high"] = max(pos["highest_high"], highs[i])
            if pos["state"] == "bull" and not np.isnan(atrs[i]):
                pos["stop"] = max(pos["stop"], pos["highest_high"] - 2.0 * atrs[i])

            exit_price = None
            exit_reason = None
            if pos["state"] == "range":
                # 触上轨后回落轨内 → 止盈；收盘有效跌破下轨 → 止损
                if not np.isnan(pos["target"]) and highs[i] >= pos["target"] and closes[i] < pos["target"]:
                    exit_price, exit_reason = closes[i], "take_profit"
                elif closes[i] < pos["stop"]:
                    exit_price, exit_reason = closes[i], "stop_loss"
            else:  # bull：吊灯止损 / 收盘破下轨清仓（任一先触发）
                if closes[i] < pos["stop"] or (not np.isnan(lowers[i]) and closes[i] < lowers[i]):
                    exit_price, exit_reason = closes[i], "stop_loss"

            if exit_price is not None:
                proceeds = shares * exit_price * (1.0 - exit_cost)
                pnl = proceeds - cash
                cash = proceeds
                shares = 0.0
                trades.append(Trade(
                    entry_date=pos["entry_date"], exit_date=dates[i], state=pos["state"],
                    entry=pos["entry"], exit=float(exit_price), pnl=float(pnl),
                    ret=float(pnl / pos["entry"]), reason=exit_reason,
                ))
                pos = None

        # 2) 新开仓：信号在 T 日，T+1 开盘成交（i < n-1）
        if pos is None and i < n - 1:
            s = sig_map.get(dates[i])
            if s is not None and not np.isnan(opens[i + 1]):
                entry = float(opens[i + 1])
                shift = entry - s.close
                stop = s.stop + shift
                target = s.target + shift
                # 用实际入场价重算盈亏比，仍不足则不出手（1e-9 容差，与 signals._make_record 一致）
                rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else -np.inf
                if rr >= cost.min_rr - 1e-9:
                    shares = 1.0 / (entry * (1.0 + entry_cost))
                    cash = 0.0
                    pos = {
                        "entry": entry, "stop": stop, "target": target,
                        "state": s.state, "highest_high": entry, "entry_date": dates[i],
                    }

    return trades, equity_curve


def _metrics(trades: List[Trade]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
                "profit_factor": None, "expectancy": None}
    pnls = np.array([t.pnl for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    win_rate = len(wins) / len(pnls)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses.mean())) if len(losses) else 0.0
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else np.inf
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    return {
        "trades": len(trades),
        "win_rate": float(win_rate),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": float(profit_factor),
        "expectancy": float(expectancy),
    }


def summarize_backtest(trades: List[Trade], equity_curve: np.ndarray) -> dict:
    """汇总回测指标（含最大回撤、夏普、分状态分段统计）。"""
    summary = _metrics(trades)

    if len(equity_curve) > 1:
        peak = np.maximum.accumulate(equity_curve)
        dd = (equity_curve - peak) / peak
        summary["max_drawdown"] = float(dd.min())
        rets = np.diff(equity_curve) / equity_curve[:-1]
        std = rets.std(ddof=1)
        summary["sharpe"] = float(rets.mean() / std * np.sqrt(252)) if std and std > 0 else None
        summary["total_return"] = float(equity_curve[-1] - 1.0)
    else:
        summary["max_drawdown"] = None
        summary["sharpe"] = None
        summary["total_return"] = 0.0

    by_state: Dict[str, dict] = {}
    for st in ("bull", "range"):
        subset = [t for t in trades if t.state == st]
        if subset:
            by_state[st] = _metrics(subset)
    summary["by_state"] = by_state
    return summary
