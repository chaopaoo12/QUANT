"""SMTP 邮件发送（GitHub Actions 定时任务用）。

参考项目（chaopaoo12/Financial_Market_Report）模式：SMTP（QQ 465 SSL 等）+ 每个报告单独一封。
凭证从环境变量读取（GitHub Secrets 注入）：SMTP_SERVER / SMTP_PORT / SMTP_FROM / SMTP_TO / SMTP_PASSWORD。
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

log = logging.getLogger("market_monitor.mailer")


def mail_config() -> dict:
    """从环境变量读取 SMTP 配置（缺任一必填项返回空 dict → 调用方跳过发送）。"""
    cfg = {
        "server": os.environ.get("SMTP_SERVER", ""),
        "port": int(os.environ.get("SMTP_PORT", "465") or 465),
        "from_addr": os.environ.get("SMTP_FROM", ""),
        "to_addr": os.environ.get("SMTP_TO", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
    }
    if not all([cfg["server"], cfg["from_addr"], cfg["to_addr"], cfg["password"]]):
        return {}
    return cfg


def _escape_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def markdown_to_html(md: str) -> str:
    """极简 md→HTML：标题 / 表格 / 加粗 / 引用 / 无序列表 / 段落。"""
    lines = md.splitlines()
    html: list[str] = ["<html><body style='font-family:Microsoft YaHei,Arial,sans-serif;font-size:13px'>"]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            html.append(f"<h{min(level,4)}>{_escape_html(line.lstrip('#').strip())}</h{min(level,4)}>")
        elif line.startswith("|"):
            # 收集连续表格行
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i]); i += 1
            html.append(_table_to_html(rows))
            continue
        elif line.startswith(">"):
            html.append(f"<blockquote style='border-left:3px solid #ccc;padding-left:8px;color:#666'>{_escape_html(line.lstrip('>').strip())}</blockquote>")
        elif re.match(r"^\s*[-*] ", line):
            item = _escape_html(re.sub(r"^\s*[-*] ", "", line))
            html.append(f"<li>{item}</li>")
        elif line.strip() == "":
            html.append("<br/>")
        else:
            body = _escape_html(line.strip())
            body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
            html.append(f"<p style='margin:2px 0'>{body}</p>")
        i += 1
    html.append("</body></html>")
    return "\n".join(html)


def _table_to_html(rows: list[str]) -> str:
    cells = []
    for r in rows:
        parts = [c.strip() for c in r.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", p) for p in parts):
            continue  # 分隔行
        cells.append("<tr>" + "".join(f"<td style='border:1px solid #ddd;padding:3px 8px'>{_escape_html(c)}</td>" for c in parts) + "</tr>")
    return ("<table style='border-collapse:collapse;margin:6px 0'>"
            + "<tbody>" + "".join(cells) + "</tbody></table>")


def send_report_email(title: str, markdown: str, server: str, port: int,
                      from_addr: str, to_addr: str, password: str) -> bool:
    """发送一封报告邮件（HTML 正文 = md 转换）。失败记日志返回 False，不抛异常。"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(title, "utf-8")
        msg["From"] = formataddr((str(Header("Market Monitor", "utf-8")), from_addr))
        msg["To"] = to_addr

        html = markdown_to_html(markdown)
        msg.attach(MIMEText(markdown, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        if port == 465:
            with smtplib.SMTP_SSL(server, port, timeout=30) as smtp:
                smtp.login(from_addr, password)
                smtp.sendmail(from_addr, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(server, port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(from_addr, password)
                smtp.sendmail(from_addr, [to_addr], msg.as_string())
        log.info("邮件发送成功：%s -> %s", title, to_addr)
        return True
    except Exception as e:  # noqa: BLE001 —— 发送失败不中断任务
        log.warning("邮件发送失败（%s）：%s", title, e)
        return False
