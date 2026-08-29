"""配置加载与外置（F5.1 / §7 非功能需求）。

所有可调参数集中在 config.yaml，凭证禁止硬编码（tushare token / 飞书 / LLM key
只从环境变量或配置文件读取，且不入库）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml


@dataclass
class CostModel:
    """交易成本模型（§5.9）。"""
    commission_rate: float = 0.00025  # 佣金 万2.5（买卖双边）
    stamp_tax_rate: float = 0.0005    # 印花税 0.05%（仅卖出）
    slippage_bp: float = 10.0         # 滑点 10bp（双边）
    min_rr: float = 1.5               # 盈亏比下限（< 此值不出手）


@dataclass
class RegimeParams:
    """状态机阈值（§5.2）。"""
    adx_trend: float = 25.0           # ADX ≥ 此值 = 趋势成立
    adx_range: float = 20.0           # ADX < 此值 = 弱趋势
    bw_trend_pct: float = 0.60        # 带宽分位 > 此值 = 趋势展开
    bw_squeeze_pct: float = 0.20      # 带宽分位 < 此值 = 收口/变盘前兆
    bw_range_pct: float = 0.50        # 带宽分位 < 此值（震荡市条件）
    confirm_bars: int = 2             # 日线状态切换防抖确认 K 线数（2–3）


@dataclass
class IndicatorParams:
    ma_fast: int = 5
    ma_10: int = 10
    ma_mid: int = 20
    ma_slow: int = 60
    boll_n: int = 20
    boll_k: float = 2.0
    bw_window: int = 250            # 带宽分位窗口
    bw_min_periods: int = 60        # 带宽分位最小样本（< 此值不输出分位/不预警）
    atr_n: int = 14
    adx_n: int = 14
    rsi_n: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    kdj_n: int = 9
    hv_window: int = 20
    hv_periods: int = 252
    volume_surge: float = 1.5       # 放量阈值（成交量 / 20 日均量）


@dataclass
class DataConfig:
    source: str = "auto"            # auto | tushare | yfinance | akshare | synthetic
    tushare_token: str = ""         # 也可用环境变量 TUSHARE_TOKEN
    tushare_endpoint: str = ""      # 第三方代理 __http_url（可选）
    proxy: str = "127.0.0.1:10808"  # yfinance 海外代理（可留空）
    cache_dir: str = "data_cache"   # 本地缓存目录
    start: str = ""                 # 回看起点（YYYYMMDD，留空则按需推算）
    trade_cal_end: str = ""         # A 股交易日历查询终点


@dataclass
class SectorParams:
    """板块判定参数（P1 / §5.5）。"""
    top_n: int = 5                  # 进攻/防守榜数量
    w_trend: float = 0.25           # 状态机维度权重
    w_rs: float = 0.25              # 相对强弱 RS 权重
    w_mf: float = 0.25              # 资金流权重
    w_turn: float = 0.25            # 换手活跃度权重
    member_limit: int = 30          # 龙头识别最多分析的成份股数
    rs_window: int = 20             # 相对强弱主窗口（日）


@dataclass
class NotifyConfig:
    """飞书通知配置（P2 / F5.4–F5.5）。"""
    feishu_webhook: str = ""        # 群机器人 webhook（推送）
    feishu_app_id: str = ""         # 开放平台 app_id（归档）
    feishu_app_secret: str = ""     # 开放平台 app_secret（归档）
    push_enabled: bool = False      # 是否启用推送


@dataclass
class ScheduleConfig:
    """调度配置（P2 / F5.3）。"""
    enabled: bool = False           # 是否注册 Windows 任务计划
    daily_time: str = "15:40"       # 日报触发（北京时间；程序内再按交易日历门控 F1.2）
    weekly_time: str = "09:30"      # 周报触发（北京时间周六上午）
    weekly_days: str = "SAT"


@dataclass
class ReportConfig:
    out_dir: str = "reports"        # 报告输出根目录


@dataclass
class Config:
    """顶层配置聚合。"""
    symbols: Dict[str, list] = field(default_factory=dict)
    cost: CostModel = field(default_factory=CostModel)
    regime: RegimeParams = field(default_factory=RegimeParams)
    indicators: IndicatorParams = field(default_factory=IndicatorParams)
    sector: SectorParams = field(default_factory=SectorParams)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    data: DataConfig = field(default_factory=DataConfig)
    report: ReportConfig = field(default_factory=ReportConfig)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Config":
        symbols = raw.get("symbols") or {}
        return cls(
            symbols=symbols,
            cost=CostModel(**(raw.get("cost") or {})),
            regime=RegimeParams(**(raw.get("regime") or {})),
            indicators=IndicatorParams(**(raw.get("indicators") or {})),
            sector=SectorParams(**(raw.get("sector") or {})),
            notify=NotifyConfig(**(raw.get("notify") or {})),
            schedule=ScheduleConfig(**(raw.get("schedule") or {})),
            data=DataConfig(**(raw.get("data") or {})),
            report=ReportConfig(**(raw.get("report") or {})),
        )

    def resolve_path(self, p: str) -> Path:
        """相对路径统一解析到项目根（config.yaml 所在目录的上级）。"""
        path = Path(p)
        if path.is_absolute():
            return path
        return self._base_dir / path


def _env_or(cfg_val: str, env_name: str) -> str:
    return os.environ.get(env_name, cfg_val)


def load_config(path: str | os.PathLike = "config.yaml") -> Config:
    """从 YAML 加载配置，并对凭证做环境变量覆盖。

    环境变量优先级：TUSHARE_TOKEN、TUSHARE_ENDPOINT、HTTPS_PROXY。
    """
    cfg_path = Path(path)
    if not cfg_path.exists():
        # 找不到时回退到包内默认配置，保证离线可运行
        cfg_path = Path(__file__).parent / "config.yaml"

    raw: Dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    cfg = Config.from_dict(raw)
    cfg._base_dir = cfg_path.parent.parent  # 项目根

    cfg.data.tushare_token = _env_or(cfg.data.tushare_token, "TUSHARE_TOKEN")
    cfg.data.tushare_endpoint = _env_or(cfg.data.tushare_endpoint, "TUSHARE_ENDPOINT")
    if not cfg.data.proxy:
        cfg.data.proxy = os.environ.get("HTTPS_PROXY", "") or os.environ.get("HTTP_PROXY", "")

    return cfg
