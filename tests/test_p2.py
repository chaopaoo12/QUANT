"""P2 调度推送测试（离线，无网络依赖）。"""
from __future__ import annotations

from market_monitor.config import Config
from market_monitor.notify import archive_to_feishu, push_feishu_card
from market_monitor.scheduler import build_schedule_commands


def test_push_without_webhook_returns_false():
    # 无 webhook：不联网、不抛异常、返回 False
    assert push_feishu_card("", "标题", "正文") is False


def test_archive_without_credentials_returns_false():
    assert archive_to_feishu("reports/x.md", "", "") is False


def test_schedule_disabled_returns_empty():
    cfg = Config()
    cfg.schedule.enabled = False
    assert build_schedule_commands(cfg) == []


def test_schedule_enabled_contains_expected():
    cfg = Config()
    cfg.schedule.enabled = True
    cmds = build_schedule_commands(cfg, python="C:\\py.exe", script="main.py", project_dir="F:\\QUANT")
    joined = "\n".join(cmds)
    assert 'schtasks /Create /TN "MarketMonitor\\Daily"' in joined
    assert '--report daily' in joined
    assert '/SC WEEKLY /D SAT' in joined
    assert '/SD "F:\\QUANT"' in joined
