"""全球金融市场监控与 A 股投研分析系统（P0 阶段）。

P0 交付范围（对应《需求分析说明书》V1.1 §9）：
    - 数据层（多源采集 + 本地缓存 + 时间口径 asof_date）
    - 指标层（MA/BOLL/MACD/ADX/ATR/RSI/KDJ/HV/带宽分位）
    - 状态机（牛市/熊市/震荡市/过渡态，单一判定流程 + 兜底 + 防抖）
    - 分状态 BOLL 择时信号 + 入场计划（次日开盘、盈亏比过滤）
    - 回测评估（含交易成本模型，分段统计）
    - 日报/周报 Markdown（手动运行）
"""

__version__ = "0.1.0"

from .config import Config, load_config
from .regime import compute_regime
from .signals import generate_signals
from .backtest import backtest_symbol, summarize_backtest
from .report import render_daily_report, render_weekly_report

__all__ = [
    "Config",
    "load_config",
    "compute_regime",
    "generate_signals",
    "backtest_symbol",
    "summarize_backtest",
    "render_daily_report",
    "render_weekly_report",
    "__version__",
]
