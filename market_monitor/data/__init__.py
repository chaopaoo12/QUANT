"""数据层：多源采集 + 本地缓存 + 时间口径（F1.1 / F1.2 / F5.2 / §2.3 / §4）。"""
from .calendar import asof_date, default_start
from .cache import Cache
from .synthetic import generate_synthetic
from .sources import DataManager, SourceError

__all__ = [
    "asof_date",
    "default_start",
    "Cache",
    "generate_synthetic",
    "DataManager",
    "SourceError",
]
