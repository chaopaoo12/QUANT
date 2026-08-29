"""多源数据采集（F1.1 / §4）：tushare（A股主源）/ yfinance（海外）/ akshare（备用）。

数据源 SDK 均为可选依赖：缺失时抛 SourceError，DataManager 捕获后回退 / 记录并跳过，
满足 F5.6「单标的失败不中断整体」。
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from .cache import Cache
from .synthetic import generate_synthetic

log = logging.getLogger("market_monitor.data")


class SourceError(Exception):
    """数据源不可用（缺 SDK / 缺 token / 网络失败）。"""


def _to_ts_code(symbol: str) -> str:
    """本地代码 → tushare ts_code（如 sh000001 → 000001.SH）。"""
    m = re.match(r"^(sh|sz|bj)(\d{6})$", symbol, re.IGNORECASE)
    if m:
        return f"{m.group(2)}.{m.group(1).upper()}"
    return symbol


class Source(ABC):
    name = "base"

    @abstractmethod
    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """返回 index=DatetimeIndex(date)，列 open/high/low/close/volume 的 DataFrame。"""

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        df = df.sort_index()
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        return df[keep].astype(float)


class TushareSource(Source):
    name = "tushare"

    def __init__(self, token: str = "", endpoint: str = ""):
        self.token = token
        self.endpoint = endpoint

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        if not self.token:
            raise SourceError("tushare token 未配置（config.data.tushare_token 或环境变量 TUSHARE_TOKEN）")
        try:
            import tushare as ts  # noqa: PLC0415
        except ImportError as e:
            raise SourceError("tushare 未安装：pip install tushare") from e

        pro = ts.pro_api(self.token)
        if self.endpoint:
            # simplify: 代理 __http_url 覆盖依赖 SDK 内部属性名，版本间可能不同，失败则用默认 endpoint
            try:
                pro._DataApi__http_url = self.endpoint  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

        ts_code = _to_ts_code(symbol)
        df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            raise SourceError(f"tushare 无数据：{symbol}")
        df = df.rename(columns={"vol": "volume", "trade_date": "date"})
        df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
        return self._normalize(df)


class YfinanceSource(Source):
    name = "yfinance"

    def __init__(self, proxy: str = ""):
        self.proxy = proxy

    @staticmethod
    def _iso(s: str) -> str:
        """YYYYMMDD → YYYY-MM-DD（yfinance 需要）。"""
        s = s.replace("-", "")
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        try:
            import yfinance as yf  # noqa: PLC0415
        except ImportError as e:
            raise SourceError("yfinance 未安装：pip install yfinance") from e

        # yfinance 的 end 为排他，+1 天以包含 end 当日
        end_excl = (pd.Timestamp(self._iso(end)) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        kwargs: dict = {"progress": False, "auto_adjust": True}
        # yfinance ≥1.5 已移除 download(proxy=...)：改走 requests.Session 传代理；
        # 传入失败（版本不支持 session）则退回无代理直连
        if self.proxy:
            try:
                import requests  # noqa: PLC0415

                session = requests.Session()
                session.proxies = {"http": "http://" + self.proxy, "https": "http://" + self.proxy}
                kwargs["session"] = session
            except Exception:  # noqa: BLE001
                kwargs.pop("session", None)
        try:
            df = yf.download(symbol, start=self._iso(start), end=end_excl, **kwargs)
        except TypeError as e:
            # 版本不支持 session/proxy 参数 → 无代理直连
            log.warning("yfinance %s 参数不支持（%s），改用无代理直连", symbol, e)
            kwargs.pop("session", None)
            df = yf.download(symbol, start=self._iso(start), end=end_excl, progress=False, auto_adjust=True)
        if df is None or df.empty:
            raise SourceError(f"yfinance 无数据：{symbol}")

        # 多标的返回 MultiIndex 列，取第一层
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        return self._normalize(df)


class AkshareSource(Source):
    """海外/国内行情 akshare 兜底（yfinance 不可用时）。

    覆盖：美股/港股指数（新浪）、外盘期货（英为财情）、美股个股（新浪）、国内期货（新浪主力合约）。
    未覆盖（外汇/加密货币，akshare 无稳定历史接口）抛 SourceError 跳过。
    """
    name = "akshare"

    _MAP = {
        "^GSPC": ("index_us", ".INX"),
        "^IXIC": ("index_us", ".IXIC"),
        "^DJI": ("index_us", ".DJI"),
        "^HSI": ("index_hk", "HSI"),
        "^HSCE": ("index_hk", "HSCEI"),
        "GC=F": ("futures", "GC"),
        "SI=F": ("futures", "SI"),
        "CL=F": ("futures", "CL"),
        "NG=F": ("futures", "NG"),
        "HG=F": ("futures", "HG"),
        "XAU=F": ("futures", "XAU"),
        # 海外知名企业（美股，新浪）
        "AAPL": ("us_stock", "AAPL"),
        "TSLA": ("us_stock", "TSLA"),
        "NVDA": ("us_stock", "NVDA"),
        "AMZN": ("us_stock", "AMZN"),
        "INTC": ("us_stock", "INTC"),
        "MSFT": ("us_stock", "MSFT"),
        "GOOGL": ("us_stock", "GOOGL"),
        "META": ("us_stock", "META"),
        "BIDU": ("us_stock", "BIDU"),
        "BABA": ("us_stock", "BABA"),
        "PDD": ("us_stock", "PDD"),
        # 国内期货（新浪主力合约）
        "ZC0": ("futures_cn", "ZC0"),
        "JM0": ("futures_cn", "JM0"),
        "I0": ("futures_cn", "I0"),
        "CU0": ("futures_cn", "CU0"),
        "P0": ("futures_cn", "P0"),
        "SR0": ("futures_cn", "SR0"),
        "CF0": ("futures_cn", "CF0"),
        "LH0": ("futures_cn", "LH0"),
    }

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        kind, code = self._MAP.get(symbol, (None, None))
        if kind is None:
            raise SourceError(f"akshare 暂不支持的标的（无历史接口）：{symbol}")
        try:
            import akshare as ak  # noqa: PLC0415
        except ImportError as e:
            raise SourceError("akshare 未安装：pip install akshare") from e

        try:
            if kind == "index_us":
                df = ak.index_us_stock_sina(symbol=code)
            elif kind == "index_hk":
                df = ak.stock_hk_index_daily_sina(symbol=code)
            elif kind == "us_stock":
                df = ak.stock_us_daily(symbol=code, adjust="qfq")
            elif kind == "futures_cn":
                df = ak.futures_main_sina(symbol=code, start_date=start, end_date=end)
            else:  # futures（英为财情）
                df = ak.futures_foreign_hist(symbol=code)
        except Exception as e:  # noqa: BLE001 —— 网页接口波动视为源失败
            raise SourceError(f"akshare {kind}:{code} 获取失败：{e}") from e

        if df is None or df.empty:
            raise SourceError(f"akshare 无数据：{symbol}")
        df = df.copy()
        df.columns = [str(c).strip().lower() for c in df.columns]
        if "日期" in df.columns:
            df = df.rename(columns={"日期": "date", "开盘价": "open", "最高价": "high",
                                    "最低价": "low", "收盘价": "close", "成交量": "volume"})
        if "date" not in df.columns:
            raise SourceError(f"akshare {symbol} 缺少 date 列：{list(df.columns)}")
        df = df.set_index(pd.to_datetime(df["date"]))
        return self._normalize(df)


class SyntheticSource(Source):
    name = "synthetic"

    def fetch_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return generate_synthetic(symbol, start, end)


def _resolve_source(symbol: str, spec_source: str) -> str:
    """解析实际数据源：'auto' 时按代码前缀推断（A股→tushare，其余→yfinance）。"""
    if spec_source and spec_source != "auto":
        return spec_source
    if re.match(r"^(sh|sz|bj)", symbol, re.IGNORECASE):
        return "tushare"
    return "yfinance"


class DataManager:
    """数据访问入口：缓存 + 主源抓取 + akshare 回退。"""

    def __init__(self, config):
        self.config = config
        self.cache = Cache(config.resolve_path(config.data.cache_dir))
        self._sources: dict = {}

    def _source(self, name: str) -> Source:
        if name not in self._sources:
            d = self.config.data
            if name == "tushare":
                self._sources[name] = TushareSource(d.tushare_token, d.tushare_endpoint)
            elif name == "yfinance":
                self._sources[name] = YfinanceSource(d.proxy)
            elif name == "akshare":
                self._sources[name] = AkshareSource()
            elif name == "synthetic":
                self._sources[name] = SyntheticSource()
            else:
                raise SourceError(f"未知数据源：{name}")
        return self._sources[name]

    def get(self, symbol: str, spec_source: str, start: str, end: str) -> Optional[pd.DataFrame]:
        source_name = _resolve_source(symbol, spec_source)
        if source_name == "synthetic":
            return generate_synthetic(symbol, start, end)

        key = f"{source_name}_{symbol}"
        cached = self.cache.load(key)
        if cached is not None and not cached.empty and cached.index.max() >= pd.Timestamp(end):
            return self._trim(cached, start, end)

        try:
            df = self._source(source_name).fetch_daily(symbol, start, end)
        except SourceError as e:
            log.warning("[%s] 主源失败：%s，尝试回退 akshare", symbol, e)
            df = self._fallback(symbol, start, end)
        except Exception as e:  # noqa: BLE001 —— 网络等边界异常，单标的失败不中断
            log.warning("[%s] 主源异常：%s，尝试回退 akshare", symbol, e)
            df = self._fallback(symbol, start, end)

        if df is not None and not df.empty:
            self.cache.update(key, df)
            return self._trim(df, start, end)
        return None

    def _fallback(self, symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
        try:
            return self._source("akshare").fetch_daily(symbol, start, end)
        except SourceError as e:
            log.warning("[%s] akshare 回退失败：%s", symbol, e)
            return None

    @staticmethod
    def _trim(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        return df.loc[(df.index >= lo) & (df.index <= hi)]
