"""板块数据源（P1 / F2.1）：同花顺概念板块全量采集。

主源 tushare（`ths_index`/`ths_daily`/`ths_member`，经代理端点），akshare 同花顺系列回退，
synthetic 离线合成（演示/测试）。本地缓存，单板块失败不中断（F5.6）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .cache import Cache
from .sources import SourceError
from .synthetic import generate_synthetic

log = logging.getLogger("market_monitor.sector_source")


@dataclass
class Sector:
    code: str
    name: str
    index_df: pd.DataFrame
    members: List[str] = field(default_factory=list)
    member_names: Dict[str, str] = field(default_factory=dict)  # code -> name


class SectorSource(ABC):
    name = "base"

    @abstractmethod
    def list_sectors(self, limit: Optional[int] = None) -> List[dict]:
        """返回 [{code, name}]。"""

    @abstractmethod
    def sector_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """板块指数日线，返回 index=date，列 open/high/low/close/volume。"""

    @abstractmethod
    def sector_members(self, code: str) -> List[str]:
        """成份股 ts_code 列表。"""

    def sector_member_names(self, code: str) -> Dict[str, str]:
        """成份股 code -> 名称；默认以 code 为名。"""
        return {c: c for c in self.sector_members(code)}

    def stock_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        raise SourceError(f"{self.name} 未实现 stock_daily")

    def stock_basic(self, ts_code: str) -> dict:
        """个股基本面快照（total_mv/amount/turnover_rate/pe_ttm/pb），默认空。"""
        return {}

    def moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """个股资金流（net_mf_amount 等），默认空。"""
        return pd.DataFrame()

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        df = df.sort_index()
        if "vol" in df.columns and "volume" not in df.columns:
            df = df.rename(columns={"vol": "volume"})
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        out = df[keep].astype(float)
        if "volume" not in out.columns:
            out["volume"] = 0.0
        return out


class TushareSectorSource(SectorSource):
    name = "tushare"

    def __init__(self, token: str = "", endpoint: str = ""):
        self.token = token
        self.endpoint = endpoint
        self._pro = None

    def _client(self):
        if self._pro is None:
            if not self.token:
                raise SourceError("tushare token 未配置（config.data.tushare_token 或 TUSHARE_TOKEN）")
            import tushare as ts  # noqa: PLC0415

            pro = ts.pro_api(self.token)
            if self.endpoint:
                try:
                    pro._DataApi__http_url = self.endpoint  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
            self._pro = pro
        return self._pro

    def list_sectors(self, limit: Optional[int] = None) -> List[dict]:
        pro = self._client()
        df = pro.ths_index(type="N")
        if df is None or df.empty:
            raise SourceError("ths_index 无数据（代理站可能未开放）")
        # 同花顺代码段：885xxx=A股概念板块（约 300+，需求 F2.1 口径）；
        # 864/865/875xxx 为美股/境外板块（ths_member 返回境外代码，pro_bar 无法取数），剔除。
        df = df[df["ts_code"].str.startswith("885")]
        if limit:
            df = df.head(limit)
        return [{"code": r.ts_code, "name": r.name} for r in df.itertuples()]

    def sector_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        pro = self._client()
        df = pro.ths_daily(ts_code=code, start_date=start, end_date=end)
        if df is None or df.empty:
            raise SourceError(f"ths_daily 无数据：{code}")
        df = df.rename(columns={"trade_date": "date"}).set_index("date")
        return self._normalize(df)

    def sector_members(self, code: str) -> List[str]:
        pro = self._client()
        df = pro.ths_member(ts_code=code)
        if df is None or df.empty:
            return []
        return list(df["con_code"])

    def sector_member_names(self, code: str) -> Dict[str, str]:
        pro = self._client()
        try:
            df = pro.ths_member(ts_code=code)
        except Exception as e:  # noqa: BLE001
            log.warning("[板块 %s] 成份名称失败：%s", code, e)
            return {}
        if df is None or df.empty:
            return {}
        return {str(r.con_code): str(r.con_name) for r in df.itertuples()}

    def stock_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        import tushare as ts  # noqa: PLC0415

        pro = self._client()
        df = ts.pro_bar(api=pro, ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            raise SourceError(f"pro_bar 无数据：{ts_code}")
        df = df.rename(columns={"trade_date": "date"}).set_index("date")
        return self._normalize(df)

    def stock_basic(self, ts_code: str) -> dict:
        pro = self._client()
        try:
            df = pro.daily_basic(ts_code=ts_code)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] daily_basic 失败：%s", ts_code, e)
            return {}
        if df is None or df.empty:
            return {}
        r = df.iloc[0]
        return {
            "total_mv": float(r.get("total_mv") or 0),
            "circ_mv": float(r.get("circ_mv") or 0),
            "amount": float(r.get("amount") or 0),
            "turnover_rate": float(r.get("turnover_rate") or 0),
            "pe_ttm": float(r.get("pe_ttm") or 0),
            "pb": float(r.get("pb") or 0),
        }

    def moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        pro = self._client()
        try:
            df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] moneyflow 失败：%s", ts_code, e)
            return pd.DataFrame()
        return df


class AkshareSectorSource(SectorSource):
    """akshare 同花顺概念板块（回退源；无稳定代码，以板块名为 code）。"""

    name = "akshare"

    def __init__(self):
        self._boards: Optional[List[dict]] = None

    def list_sectors(self, limit: Optional[int] = None) -> List[dict]:
        import akshare as ak  # noqa: PLC0415

        if self._boards is None:
            df = ak.stock_board_concept_name_ths()
            self._boards = [{"code": r["概念名称"], "name": r["概念名称"]} for _, r in df.iterrows()]
        if limit:
            return self._boards[:limit]
        return self._boards

    def sector_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_board_concept_hist_ths(symbol=code, start_date=start, end_date=end)
        if df is None or df.empty:
            raise SourceError(f"akshare ths 无数据：{code}")
        df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                                "最低": "low", "收盘": "close", "成交量": "volume"})
        df = df.set_index("date")
        return self._normalize(df)

    def sector_members(self, code: str) -> List[str]:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_board_concept_cons_ths(symbol=code)
        if df is None or df.empty:
            return []
        return list(df["代码"])

    def stock_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_zh_a_hist(symbol=ts_code, period="daily",
                                start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            raise SourceError(f"akshare 个股无数据：{ts_code}")
        df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                                "最低": "low", "收盘": "close", "成交量": "volume"})
        df = df.set_index("date")
        return self._normalize(df)


def generate_synthetic_sectors(start: str, end: str, n_sectors: int = 12,
                               n_stocks: int = 60, seed: int = 42) -> tuple[list, Dict[str, pd.DataFrame]]:
    """离线合成板块：n_stocks 只合成个股 + n_sectors 个概念板块（等权指数、成份重叠）。"""
    rng = np.random.default_rng(seed)
    stock_dfs: Dict[str, pd.DataFrame] = {
        f"S{i:04d}": generate_synthetic(f"S{i:04d}", start, end) for i in range(n_stocks)
    }
    first_idx = next(iter(stock_dfs.values())).index
    sectors: list = []
    for s in range(n_sectors):
        members = [f"S{i:04d}" for i in rng.choice(n_stocks, size=min(25, n_stocks), replace=False)]
        closes = np.column_stack([stock_dfs[c]["close"].values for c in members])
        norm = closes / closes[0]
        idx_close = 100.0 * norm.mean(axis=1)
        vols = np.column_stack([stock_dfs[c]["volume"].values for c in members]).sum(axis=1)
        opens = idx_close * (1.0 + rng.normal(0.0, 0.003, len(idx_close)))
        highs = np.maximum(opens, idx_close) * 1.008
        lows = np.minimum(opens, idx_close) * 0.992
        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": idx_close, "volume": vols},
            index=first_idx,
        )
        df.index.name = "date"
        sectors.append({"code": f"SYN{s:03d}", "name": f"合成概念{s + 1}", "index_df": df, "members": members})
    return sectors, stock_dfs


class SyntheticSectorSource(SectorSource):
    name = "synthetic"

    def __init__(self, n_sectors: int = 12, n_stocks: int = 60):
        self.n_sectors = n_sectors
        self.n_stocks = n_stocks
        self._cache_data: Optional[tuple] = None

    def _ensure(self, start: str, end: str):
        if self._cache_data is None:
            self._cache_data = generate_synthetic_sectors(start, end, self.n_sectors, self.n_stocks)
        return self._cache_data

    def list_sectors(self, limit: Optional[int] = None) -> List[dict]:
        sectors, _ = self._ensure("20240101", "20261231")
        if limit:
            sectors = sectors[:limit]
        return [{"code": s["code"], "name": s["name"]} for s in sectors]

    def sector_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        sectors, _ = self._ensure("20240101", "20261231")
        for s in sectors:
            if s["code"] == code:
                df = s["index_df"]
                return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        raise SourceError(f"synthetic sector 不存在：{code}")

    def sector_members(self, code: str) -> List[str]:
        sectors, _ = self._ensure("20240101", "20261231")
        for s in sectors:
            if s["code"] == code:
                return s["members"]
        return []

    def stock_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        _, stock_dfs = self._ensure("20240101", "20261231")
        df = stock_dfs.get(ts_code)
        if df is None:
            raise SourceError(f"synthetic stock 不存在：{ts_code}")
        return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]

    def stock_basic(self, ts_code: str) -> dict:
        _, stock_dfs = self._ensure("20240101", "20261231")
        df = stock_dfs.get(ts_code)
        if df is None:
            return {}
        amount = float((df["close"] * df["volume"]).mean())
        return {"total_mv": amount, "circ_mv": amount, "amount": amount,
                "turnover_rate": 2.0, "pe_ttm": 20.0, "pb": 2.0}

    def moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        _, stock_dfs = self._ensure("20240101", "20261231")
        df = stock_dfs.get(ts_code)
        if df is None:
            return pd.DataFrame()
        sub = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        net = sub["close"].pct_change().fillna(0) * sub["volume"]
        return pd.DataFrame({"net_mf_amount": net.values}, index=sub.index)


class SectorStore:
    """板块访问入口：列表/行情/成份/个股，带缓存与单板块容错。"""

    def __init__(self, source: SectorSource, cache_dir):
        self.source = source
        self.cache = Cache(cache_dir)

    def list_sectors(self, start: str, end: str, limit: Optional[int] = None) -> List[Sector]:
        out: List[Sector] = []
        for m in self.source.list_sectors(limit):
            key = f"sector_{m['code']}"
            df = self.cache.load(key)
            if df is None or len(df) == 0 or df.index.max() < pd.Timestamp(end):
                try:
                    df = self.source.sector_daily(m["code"], start, end)
                    if df is not None and len(df):
                        self.cache.update(key, df)
                except Exception as e:  # noqa: BLE001
                    log.warning("[板块 %s] 行情获取失败：%s", m["code"], e)
                    continue
            if df is None or len(df) < 60:
                log.warning("[板块 %s] 数据不足，跳过", m["code"])
                continue
            members, member_names = self._members(m["code"])
            out.append(Sector(code=m["code"], name=m["name"], index_df=df,
                              members=members, member_names=member_names))
        return out

    def _members(self, code: str) -> tuple[List[str], Dict[str, str]]:
        key = f"sector_members_{code}"
        cached = self.cache.load(key)
        if cached is not None and len(cached):
            members = list(cached["member"])
            names = dict(zip(members, cached.get("name", members))) if "name" in cached.columns else {}
            return members, names
        try:
            members = self.source.sector_members(code)
            names = self.source.sector_member_names(code) or {m: m for m in members}
        except Exception as e:  # noqa: BLE001
            log.warning("[板块 %s] 成份获取失败：%s", code, e)
            return [], {}
        if members:
            self.cache.save(key, pd.DataFrame(
                {"member": members, "name": [names.get(m, m) for m in members]}))
        return members, names

    def stock_daily(self, ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        key = f"stock_{ts_code}"
        df = self.cache.load(key)
        if df is None or len(df) == 0 or df.index.max() < pd.Timestamp(end):
            try:
                df = self.source.stock_daily(ts_code, start, end)
                if df is not None and len(df):
                    self.cache.update(key, df)
            except Exception as e:  # noqa: BLE001
                log.warning("[个股 %s] 行情获取失败：%s", ts_code, e)
                return None
        return df

    def stock_basic(self, ts_code: str) -> dict:
        return self.source.stock_basic(ts_code)

    def moneyflow(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        return self.source.moneyflow(ts_code, start, end)


def make_sector_store(config, source: str = "auto", n_sectors: int = 12) -> SectorStore:
    """按配置构建 SectorStore：auto→tushare；tushare 失败调用方自行回退 akshare/synthetic。"""
    d = config.data
    if source == "auto":
        source = "tushare"
    if source == "tushare":
        src: SectorSource = TushareSectorSource(d.tushare_token, d.tushare_endpoint)
    elif source == "akshare":
        src = AkshareSectorSource()
    elif source == "synthetic":
        src = SyntheticSectorSource(n_sectors=n_sectors)
    else:
        raise ValueError(f"未知板块数据源：{source}")
    return SectorStore(src, config.resolve_path(d.cache_dir))
