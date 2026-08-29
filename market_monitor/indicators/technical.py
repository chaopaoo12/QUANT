"""技术指标实现（§5.1 指标计算规则）。

全部为纯函数，输入 pandas.DataFrame（index 为日期，含列 open/high/low/close/volume），
返回 Series 或 DataFrame。不产生未来函数：每个指标在 t 时刻仅使用 ≤t 的数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder_smooth(values: pd.Series, n: int) -> pd.Series:
    """Wilder 平滑（等价于 ewm(alpha=1/n, adjust=False)，首值用均值填充）。"""
    return values.ewm(alpha=1.0 / n, adjust=False).mean()


def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """BOLL：中轨 = MA(n)，上下轨 = 中轨 ± k×Std(n)。"""
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "std": std})


def bandwidth(boll: pd.DataFrame) -> pd.Series:
    """BOLL 带宽 = (上轨 − 下轨) / 中轨。"""
    return (boll["upper"] - boll["lower"]) / boll["mid"]


def bandwidth_percentile(bw: pd.Series, window: int = 250, min_periods: int = 60) -> pd.Series:
    """带宽分位 = 当日带宽在近 window 个交易日中的百分位。

    最小样本 min_periods：不足 window 按可得样本计算；< min_periods 输出 NaN
    （下游据此不发收口预警，见 §5.1）。
    """
    def _pct(x: np.ndarray) -> float:
        # 当日值在窗口内的位置 / (窗口长度 - 1)，0 = 最低，1 = 最高
        if len(x) < 2:
            return np.nan
        return float((x < x[-1]).sum()) / (len(x) - 1)

    out = bw.rolling(window, min_periods=min_periods).apply(_pct, raw=True)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = 2.0 * (dif - dea)
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist})


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder_smooth(gain, n)
    avg_loss = _wilder_smooth(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # 无下跌时 RSI=100
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~(avg_gain.isna()), np.nan)
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder ATR（14 日平均真实波幅）。"""
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return _wilder_smooth(tr, n)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    """Wilder ADX（趋势强度，不含方向），返回 +DI/-DI/ADX。"""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    atr_s = _wilder_smooth(tr, n)
    plus_di = 100.0 * _wilder_smooth(plus_dm, n) / atr_s
    minus_di = 100.0 * _wilder_smooth(minus_dm, n) / atr_s

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_s = _wilder_smooth(dx, n)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": adx_s})


def kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """KDJ(9,3,3)。"""
    low_n = low.rolling(n, min_periods=n).min()
    high_n = high.rolling(n, min_periods=n).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0.0, np.nan) * 100.0
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return pd.DataFrame({"k": k, "d": d, "j": j})


def historical_volatility(close: pd.Series, window: int = 20, periods: int = 252) -> pd.Series:
    """历史波动率 HV = 日收益率标准差 × √periods。"""
    returns = close.pct_change()
    return returns.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(periods)


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线 → 周线（按周收盘聚合，海外 yfinance 周线 resample 同口径）。"""
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index(pd.to_datetime(df.index))
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return df.resample("W-FRI").agg(agg).dropna(subset=["close"])


def add_all_indicators(df: pd.DataFrame, params=None) -> pd.DataFrame:
    """在日线 DataFrame 上计算全部指标，返回带指标的副本。

    params: IndicatorParams（缺省用默认值）。
    """
    from ..config import IndicatorParams

    p = params or IndicatorParams()
    out = df.copy()

    # 均线（MA5/10/20/60，§5.1）
    for n, name in [(p.ma_fast, "ma5"), (p.ma_10, "ma10"), (p.ma_mid, "ma20"), (p.ma_slow, "ma60")]:
        out[name] = sma(out["close"], n)

    # BOLL + 带宽 + 分位
    boll = bollinger(out["close"], p.boll_n, p.boll_k)
    out["boll_mid"] = boll["mid"]
    out["boll_upper"] = boll["upper"]
    out["boll_lower"] = boll["lower"]
    out["bw"] = bandwidth(boll)
    out["bw_pct"] = bandwidth_percentile(out["bw"], p.bw_window, p.bw_min_periods)

    # MACD
    m = macd(out["close"], p.macd_fast, p.macd_slow, p.macd_signal)
    out["dif"] = m["dif"]
    out["dea"] = m["dea"]
    out["macd_hist"] = m["hist"]

    # 波动/趋势
    out["atr"] = atr(out["high"], out["low"], out["close"], p.atr_n)
    a = adx(out["high"], out["low"], out["close"], p.adx_n)
    out["adx"] = a["adx"]
    out["plus_di"] = a["plus_di"]
    out["minus_di"] = a["minus_di"]

    # RSI / KDJ / HV
    out["rsi"] = rsi(out["close"], p.rsi_n)
    k = kdj(out["high"], out["low"], out["close"], p.kdj_n)
    out["kdj_k"] = k["k"]
    out["kdj_d"] = k["d"]
    out["kdj_j"] = k["j"]
    out["hv"] = historical_volatility(out["close"], p.hv_window, p.hv_periods)

    # 均量（放量判断用）
    out["vol_ma20"] = out["volume"].rolling(20, min_periods=20).mean()

    return out
