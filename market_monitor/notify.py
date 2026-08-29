"""飞书推送与归档（P2 / F5.4–F5.5 / §6）。

- 推送：群自定义机器人 webhook，消息卡片（标题 + Markdown 正文）；
- 归档：飞书开放平台 docx/wiki API（需 app_id/secret）；无凭证时降级本地 reports/ 目录。

任何一步失败只记日志不中断（容错，F5.6）。
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("market_monitor.notify")


def push_feishu_card(webhook: str, title: str, text: str) -> bool:
    """推送飞书消息卡片。无 webhook / 失败返回 False。"""
    if not webhook:
        log.info("未配置飞书 webhook，跳过推送")
        return False
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        log.warning("requests 未安装，跳过推送")
        return False

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title[:100]}},
            "elements": [{"tag": "markdown", "content": text[:4000]}],
        },
    }
    try:
        resp = requests.post(webhook, json=card, timeout=10)
        ok = resp.status_code == 200 and resp.json().get("code", resp.json().get("StatusCode", 1)) == 0
        if not ok:
            log.warning("飞书推送失败：%s %s", resp.status_code, resp.text[:200])
        return ok
    except Exception as e:  # noqa: BLE001 —— 网络/服务异常不中断
        log.warning("飞书推送异常：%s", e)
        return False


def archive_to_feishu(md_path: str | Path, app_id: str, app_secret: str) -> bool:
    """归档 Markdown 报告到飞书文档库（docx）。

    simplify: 完整导入（tenant_access_token → 创建文档 → Markdown 转 blocks 上传）需开放平台
    权限开通并做一次真实联调；当前实现：无凭证返回 False（报告已本地落盘），
    有凭证则给出接入提示，避免伪造"已归档"。
    """
    path = Path(md_path)
    if not app_id or not app_secret:
        log.info("未配置飞书归档凭证（app_id/app_secret），报告已保存本地：%s", path)
        return False
    log.warning(
        "飞书 docx 归档待接入：app_id 已配置，但 Markdown→blocks 导入需开通云文档/知识库权限后联调（见需求 F5.5）。报告已保存本地：%s",
        path,
    )
    return False


def push_report_if_enabled(config, title: str, text: str) -> bool:
    """按配置推送（notify.push_enabled 开关）。"""
    if not getattr(config, "notify", None) or not config.notify.push_enabled:
        return False
    return push_feishu_card(config.notify.feishu_webhook, title, text)
