"""交易日历与时间口径（F1.2 / §2.3）。

- asof_date：每个标的的「数据日期」= 该 DataFrame 最新收盘日（不做跨市场强行对齐）；
- A 股交易日判断可调用 tushare `trade_cal`（可选）；海外标的以「数据日期有更新」触发。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd


def asof_date(df: pd.DataFrame):
    """返回 df 的最新交易日（asof_date）。"""
    if df is None or len(df) == 0:
        return None
    idx = pd.to_datetime(df.index)
    return idx.max()


def default_start(end: str | dt.date, calendar_days: int = 700) -> str:
    """按回看日历天数推算起点（覆盖约 300 交易日 + 250 日带宽分位窗口余量）。

    simplify: 用自然日近似，未按交易日历精确回推；对 250 日分位窗口足够。
    """
    if isinstance(end, str):
        end_d = dt.datetime.strptime(end, "%Y%m%d").date()
    else:
        end_d = end
    start_d = end_d - dt.timedelta(days=calendar_days)
    return start_d.strftime("%Y%m%d")


def is_a_share_trading_day(date: str, token: str = "") -> bool | None:
    """判断 A 股是否交易日（tushare trade_cal）。

    无 token / tushare 未安装时返回 None（调用方自行降级：以数据更新触发）。
    """
    try:
        import tushare as ts  # noqa: PLC0415
    except ImportError:
        return None
    if not token:
        return None
    try:
        pro = ts.pro_api(token)
        df = pro.trade_cal(exchange="SSE", start_date=date, end_date=date,
                           fields="cal_date,is_open")
        if df is None or df.empty:
            return None
        return int(df.iloc[0]["is_open"]) == 1
    except Exception:  # noqa: BLE001 —— 网络/权限异常时降级
        return None
