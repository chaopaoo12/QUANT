"""邮件模块测试（离线）。"""
from __future__ import annotations

from market_monitor.mailer import mail_config, markdown_to_html


def test_markdown_to_html_structure():
    md = "# 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n**加粗** 正文"
    h = markdown_to_html(md)
    assert "<h1>" in h and "标题" in h
    assert "<table" in h and "<td" in h
    assert "<b>加粗</b>" in h


def test_mail_config_empty_without_env(monkeypatch):
    for k in ("SMTP_SERVER", "SMTP_PORT", "SMTP_FROM", "SMTP_TO", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert mail_config() == {}


def test_mail_config_reads_env(monkeypatch):
    monkeypatch.setenv("SMTP_SERVER", "smtp.qq.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_FROM", "a@qq.com")
    monkeypatch.setenv("SMTP_TO", "b@qq.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    cfg = mail_config()
    assert cfg["server"] == "smtp.qq.com" and cfg["to_addr"] == "b@qq.com"
