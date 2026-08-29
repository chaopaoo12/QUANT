---
name: market-monitor
description: Use when operating, debugging, or extending the market_monitor Python package in this repo — running daily/weekly reports or backtests, changing the state machine, BOLL signals, indicators, or data sources, or diagnosing why a symbol has no signal/data. Do not use for generic pandas/quant advice unrelated to this package.
---

# Market Monitor 操作与扩展

## Load Order

先读仓库根 `README.md`（安装 / 命令 / 结构）与《需求分析说明书》对应章节（`全球市场监控系统-需求分析说明书.md`），再按本 skill 操作。本包源码即权威实现；冲突时以源码 + 需求说明书为准。

## Scope

本 skill 覆盖 `market_monitor/` 这个 Python 包（**P0–P4 全部分期**）：怎么跑各类报告、怎么读、怎么安全地改状态机/信号/指标/板块/财报/IPO/数据源。各模块：P0（indicators/regime/signals/backtest）、P1（sectors + data/sector_source）、P2（notify/scheduler）、P3（financials）、P4（ipo）。不要臆造需求说明书之外的逻辑。

## Workflow

**跑一次分析（主路径）**，顺序固定：

1. 定数据源：离线演示用 `--source synthetic`（各期也可单独 `--*-source synthetic`）；真实数据走 `config.yaml` 已配置的 tushare token/端点 + yfinance 代理。
2. 跑对应报告：
   ```bash
   python main.py --source synthetic --report daily          # P0 大盘日报
   python main.py --report sector --sector-source synthetic  # P1 板块日报（合成）
   python main.py --report financial --fin-source synthetic  # P3 财报扫描（合成）
   python main.py --report ipo --ipo-source synthetic        # P4 IPO 专刊（合成）
   python main.py --report daily                             # 真实数据（tushare/yfinance）
   ```
3. 读输出：报告落在 `reports/YYYY-MM-DD/{daily,weekly,sector,sector-weekly,financial,ipo}.md`，同时打印到 stdout。

**改动后必跑的自检（顺序）**：

```bash
python -m market_monitor.selfcheck     # 端到端不变量（P0–P4 全部检查）
python -m pytest tests/ -q             # 单元测试
```

**扩展一个环节的标准顺序**（数据 → 指标 → 状态机 → 信号 → 回测 → 板块/财报/IPO → 报告）：

1. 改哪个模块，先读该模块全文 + 调用它的 `cli.py` 编排路径；
2. 加/改逻辑后，在对应 `tests/test_*.py` 或 `selfcheck.py` 补一个最小断言；
3. 跑自检 + 测试通过后才算完成。

## Decision Rules（不变量，不可违反）

- **无未来函数**：信号 T 日收盘确认、T+1 开盘成交（`shift(1)`）；周线状态只用已收盘周（`resample("W-FRI")` + `ffill`）。任何改动不得用 ≤t 之外的数据。
- **状态层级分离**：信号触发只用「日线确认状态」（`df["state"]`，防抖后）；`weekly_state` 仅作展示/强确认，不得反过来驱动信号。
- **盈亏比过滤**：`rr < cost.min_rr`（默认 1.5）必须 `skipped`；回测用实际次日开盘重算盈亏比。
- **净成本口径**：佣金 + 印花税 + 滑点必须计入回测盈亏；不得用毛收益宣称结果。
- **凭证不硬编码**：token/key 只从 `config.yaml` 或环境变量读，禁止写进源码或提交。
- **单标的失败不中断**：数据获取/分析异常须捕获并记日志继续，不得让一个标的中断整批。
- **A 股代码过滤**：龙头识别只用 `\d{6}.(SH|SZ|BJ)` 成员（ths_member 可能混入境外股，`pro_bar` 不支持）。
- **财报口径**：四象限主判用单季同比（`netprofit_yoy`）；「超预期」无一致预期源时用代理口径并在报告中标注。
- **无凭证降级**：飞书推送/归档、LLM 提取无凭证时必须优雅降级（本地保存/明确提示），不得伪造成功。
- **P1–P4 归属**：板块/财报/IPO/调度分别在 `sectors.py`/`financials.py`/`ipo.py`/`notify.py`+`scheduler.py`，改动不要跨模块乱放。

## Evidence（正确结果长什么样）

- `python -m market_monitor.selfcheck` 打印两行 `[selfcheck OK]`（P0 指标 + P1 板块/P3 财报/P4 IPO 数量），无异常。
- `pytest` 全绿（P0–P4 各期测试文件）。
- 报告文件存在且含对应章节标题（`# 大盘日报` / `# 周报` / `# 板块日报` / `# 财报分析` / `# IPO 估值预判`）。
- 改动若影响信号/回测，能给出「改动前 vs 改动后」的指标对比（如信号数、盈亏因子变化）。

## References

| 何时打开 | 文件 |
|---|---|
| 命令清单、真实数据接入、扩展配方（新增指标/信号/数据源示例） | `references/commands.md` |
| 需求规则原文（状态机/择时/回测阈值） | 仓库根 `全球市场监控系统-需求分析说明书.md` §5 |
| 包结构、安装、已知简化 | 仓库根 `README.md` |
