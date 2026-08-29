"""本地数据缓存（F5.2）：parquet 落盘（缺失 pyarrow 时回退 CSV），增量合并去重。"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


class Cache:
    def __init__(self, cache_dir: str | Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_key(key: str) -> str:
        return re.sub(r"[^0-9A-Za-z_.-]", "_", key)

    def _path(self, key: str, ext: str) -> Path:
        return self.dir / f"{self._safe_key(key)}.{ext}"

    def load(self, key: str) -> pd.DataFrame | None:
        parquet = self._path(key, "parquet")
        csv = self._path(key, "csv")
        try:
            if parquet.exists():
                df = pd.read_parquet(parquet)
                return df
            if csv.exists():
                return pd.read_csv(csv, index_col=0, parse_dates=True)
        except Exception:  # noqa: BLE001 —— 缓存损坏时视为未命中，重新拉取
            return None
        return None

    def save(self, key: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        try:
            df.to_parquet(self._path(key, "parquet"))
        except Exception:  # noqa: BLE001 —— 无 pyarrow 时回退 CSV
            df.to_csv(self._path(key, "csv"))

    def update(self, key: str, df: pd.DataFrame) -> pd.DataFrame:
        """增量更新：与现有缓存按日期合并去重、排序后落盘，返回合并结果。"""
        existing = self.load(key)
        if existing is not None and not existing.empty:
            df = pd.concat([existing, df])
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.sort_index()
        else:
            df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        self.save(key, df)
        return df
