"""Windows 任务计划生成（P2 / F5.3 / §7 调度）。

schtasks 注册：日报每天定时触发、周报每周六上午触发；「每交易日」由程序内部按 A 股
交易日历门控（F1.2：非交易日直接退出，避免节假日空跑）。IPO 事件触发器在 P4 接入数据源后启用。
"""
from __future__ import annotations

import shlex
from typing import List

from .config import Config


def build_schedule_commands(config: Config, python: str = "python",
                            script: str = "main.py", project_dir: str = "") -> List[str]:
    """生成 schtasks 注册命令列表（不实际注册，供用户/脚本执行）。

    project_dir：项目根（脚本所在目录），用于设置工作目录（/SD 参数）。
    """
    if not config.schedule.enabled:
        return []

    py = shlex.quote(python)
    sc = shlex.quote(script)
    sd = f' /SD "{project_dir}"' if project_dir else ""
    cmds: List[str] = []

    cmds.append(
        f'schtasks /Create /TN "MarketMonitor\\Daily" /TR "{py} {sc} --report daily" '
        f'/SC DAILY /ST {config.schedule.daily_time} /F{sd}'
    )
    cmds.append(
        f'schtasks /Create /TN "MarketMonitor\\Weekly" /TR "{py} {sc} --report weekly" '
        f'/SC WEEKLY /D {config.schedule.weekly_days} /ST {config.schedule.weekly_time} /F{sd}'
    )
    cmds.append('echo [提示] 任务计划已注册：Daily 每天 %s 触发、Weekly %s %s 触发（非交易日由程序内日历门控）'
                % (config.schedule.daily_time, config.schedule.weekly_days, config.schedule.weekly_time))
    cmds.append('echo [提示] 删除任务：schtasks /Delete /TN "MarketMonitor\\Daily" /F 等')
    return cmds


def is_a_share_trading_day_gate(config: Config, end: str) -> bool:
    """交易日门控：非 A 股交易日直接跳过（调度任务内自检）。

    simplify: 仅当 tushare token 可用时判断；否则默认放行（依赖外部调度）。
    """
    from .data.calendar import is_a_share_trading_day

    verdict = is_a_share_trading_day(end, config.data.tushare_token)
    if verdict is None:
        return True
    return verdict
