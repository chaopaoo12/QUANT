# 全球金融市场监控与 A 股投研分析系统（P0–P4）

依据《全球市场监控系统-需求分析说明书》V1.1 实现的 Python 包，**P0–P4 全部分期已开发并测试**。

P0 交付范围（对应说明书 §9）：
- 数据层：多源采集（tushare / yfinance / akshare 备用）+ 本地缓存 + **时间口径 `asof_date`**（§2.3）
- 指标层：MA / BOLL / 带宽分位 / MACD / ADX / ATR / RSI / KDJ / HV（§5.1）
- 状态机：牛市/熊市/震荡市/过渡态，单一判定流程 + 兜底 + 防抖 + 状态层级分离（§5.2）
- 分状态 BOLL 择时 + 入场计划（次日开盘、盈亏比 ≥1.5、可成交性过滤）（§5.3）
- 回测评估（次日持仓、含交易成本模型、分状态统计）（§5.9）
- 日报 / 周报 Markdown，手动运行（§6）

后续分期（均已实现 + 测试）：
- **P1 板块与龙头**：`sectors.py` + `data/sector_source.py` —— 同花顺概念板块全量采集（tushare `ths_*` 主源/akshare 回退/合成离线）、进攻/防守四维打分 Top5、龙头识别（情绪/行业）、板块择时（复用状态机）、板块日报/周报
- **P2 调度推送**：`notify.py`（飞书 webhook 卡片推送 + 文档库归档，无凭证降级本地）、`scheduler.py`（Windows 任务计划 schtasks 生成 + 交易日门控）
- **P3 财报分析**：`financials.py` —— 披露检测（ann_date）、龙头板块跟进（主线强化/退潮预警）、全市场景气-估值四象限、财报后 1/3/5 日反应、财报专刊
- **P4 IPO 估值**：`ipo.py` —— 新股数据（new_share/合成）、可比估值区间、发行定价判断（注册制）、DCF 粗算、质量打分与评级、IPO 专刊

## 安装

```bash
pip install -r requirements.txt
pip install tushare            # A股/板块/财报/IPO 主源（token 已配置于 config.yaml）
# yfinance akshare 已装；缺失时对应 source 优雅降级
```

## 快速开始

### 1) 离线演示（无需 token / 网络，合成行情跑通全链路）

```bash
python main.py --source synthetic --report daily
python main.py --source synthetic --report weekly --end 20260826
```

### 2) 端到端自检（断言关键不变量）

```bash
python -m market_monitor.selfcheck
```

### 3) 单元测试

```bash
python -m pytest tests/ -q
```

### 4) 真实数据

```bash
# tushare token 已配置于 config.yaml（或用环境变量 TUSHARE_TOKEN 覆盖）
python main.py --report daily
```

## 命令

```
python main.py --end YYYYMMDD [--report daily|weekly|sector|sector-weekly|financial|ipo]
              [--source auto|tushare|yfinance|akshare|synthetic]
              [--sector-source ...] [--sector-limit N]
              [--fin-source ...] [--fin-universe N] [--fin-boards N]
              [--ipo-source ...] [--ipo-limit N]
```

- `--end`：目标日期（默认今天）
- `--report`：daily / weekly / sector（板块日报）/ sector-weekly（板块周报）/ financial（财报扫描）/ ipo（IPO 专刊）
- `--source synthetic`：全链路离线跑通；各期另有 `--*-source synthetic` 单独离线
- `--sector-limit`：真实板块数量上限（演示建议 20–50）；`--fin-boards`：财报板块景气聚合板块数

报告输出到 `reports/YYYY-MM-DD/{daily,weekly,sector,sector-weekly,financial,ipo}.md`。

## 目录结构

```
market_monitor/
  config.py / config.yaml    配置外置（tushare token/端点、飞书凭证、阈值）
  indicators/technical.py    技术指标（纯函数）
  regime.py                  市场状态机
  signals.py                 分状态 BOLL 择时 + 入场计划
  backtest.py                回测 + 交易成本模型 + 分段指标
  sectors.py                 P1 板块进攻/防守 + 龙头 + 板块择时
  financials.py              P3 财报披露/四象限/后反应
  ipo.py                     P4 IPO 估值/定价/DCF/评分
  notify.py                  P2 飞书推送/归档
  scheduler.py               P2 Windows 任务计划生成
  report.py                  全部报告 Markdown
  data/
    calendar.py              交易日历 + asof_date
    cache.py                 parquet 本地缓存
    sources.py               tushare/yfinance/akshare 数据源
    sector_source.py         P1 板块数据源（tushare/akshare/合成）
    synthetic.py             合成行情（离线演示）
  cli.py                     编排与 CLI
  selfcheck.py               端到端自检（P0–P4）
main.py                      入口
tests/                       单元测试（P0–P4）
skills/market-monitor/       agent skill（操作/扩展本包的说明）
```

## GitHub Actions 定时任务 + 邮件

推送到 GitHub 后，工作流自动按计划执行并邮件发送报告（每个报告单独一封）：

| 工作流 | 触发（北京时间） | 内容 |
|---|---|---|
| `.github/workflows/daily_report.yml` | 每天 **08:00 / 20:00** | 大盘日报 + 板块日报（各一封）+ 龙头板块财报跟进（**进攻榜当日有新披露才发**） |
| `.github/workflows/weekend_ipo.yml` | 周末 **09:00** | IPO 估值预判 |

**需要在 GitHub 仓库 Settings → Secrets and variables → Actions 配置以下 Secrets：**

| Secret | 说明 |
|---|---|
| `TUSHARE_TOKEN` | tushare token（A股/板块/财报/IPO 数据） |
| `SMTP_SERVER` | 例如 `smtp.qq.com` |
| `SMTP_PORT` | 例如 `465`（SSL）或 `587`（STARTTLS） |
| `SMTP_FROM` | 发件邮箱（QQ 邮箱需开启 SMTP 并填入**授权码**，非登录密码） |
| `SMTP_TO` | 收件邮箱（可多个，逗号分隔） |
| `SMTP_PASSWORD` | SMTP 授权码 |

**本地调试**（不配置 SMTP 环境变量时只生成报告不发邮件）：
```bash
python scheduled_reports.py --task ipo --end 20260829
python scheduled_reports.py --task daily --sector-boards 20
```
> 注：`--task daily` 的板块龙头识别需拉取成份股行情，耗时约 10–20 分钟（GitHub Actions 免费额度按分钟计费，可调小 `--sector-boards`）。海外行情在 Actions 上若 yfinance 被限流会自动回退 akshare（美股/港股/期货），外汇/加密货币无备用源会被跳过。

## 关键设计决策

1. **无未来函数**：信号 T 日收盘确认、T+1 开盘成交（`shift(1)`）；周线状态只用已收盘周（`W-FRI` resample + `ffill`）。
2. **状态层级分离**：信号触发用「日线确认状态」（防抖后）；「周线状态」仅作强确认，报告分列展示。
3. **盈亏比过滤**：入场计划用信号日收盘估算盈亏比，回测用实际次日开盘重算，`< 1.5` 不出手。
4. **净成本口径**：佣金万 2.5 + 卖出印花税 0.05% + 滑点 10bp，回测指标全部按扣成本后计算。
5. **容错隔离**：单标的失败不中断整体（F5.6），主源失败自动回退 akshare 并记日志。

## 已知简化（simplify: 标记于源码）

- 离场成交价用当日收盘（非次日开盘），无未来函数。
- 「跌破 MA10 减半仓 / 跌破中轨减仓」P0 按整仓退出模拟（未做半仓分级）。
- 周线 W_MA60 需约 60 周历史，早期周线状态为「震荡」（中性）。
- tushare 第三方代理 endpoint 覆盖依赖 SDK 内部属性，版本间可能不同。

## 路线图（后续分期，见说明书 §9）

- **P1** 板块与龙头：ths_index 全量、进攻/防守判定、龙头识别、板块择时
- **P2** 调度推送：Windows 任务计划 + 飞书机器人 + 飞书文档库归档
- **P3** 财报：披露检测、龙头板块跟进、全市场景气-估值四象限
- **P4** IPO：招股书抓取 + LLM 解析 + 估值预判

> 免责声明：本系统输出仅供个人研究参考，不构成投资建议。
