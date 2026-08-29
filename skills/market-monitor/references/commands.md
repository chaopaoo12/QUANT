# Market Monitor — 命令与扩展配方

## 常用命令

```bash
# 离线演示（合成行情，无需 token/网络）
python main.py --source synthetic --report daily
python main.py --source synthetic --report weekly --end 20260826

# 自检 + 单测
python -m market_monitor.selfcheck
python -m pytest tests/ -q

# 真实数据（A股 tushare / 海外 yfinance）
set TUSHARE_TOKEN=你的token          # Windows；或 export TUSHARE_TOKEN=...
python main.py --report daily
```

参数：`--end YYYYMMDD`、`--report daily|weekly`、`--config config.yaml`、`--source auto|tushare|yfinance|akshare|synthetic`、`--verbose`。

## 真实数据接入

- **tushare token**：环境变量 `TUSHARE_TOKEN` 或 `config.yaml` 的 `data.tushare_token`；第三方代理 `data.tushare_endpoint`。
- **yfinance 代理**：`config.yaml` 的 `data.proxy`（默认 `127.0.0.1:10808`）。
- **标的新增**：在 `config.yaml` 的 `symbols` 下加 `{symbol, name, source}`；A股代码用 `sh000001` 形式（自动转 `000001.SH`）。

## 扩展配方

### 新增一个指标

1. 在 `market_monitor/indicators/technical.py` 加纯函数（输入 `close/high/low/close` 等 Series，返回 Series）。
2. 在 `add_all_indicators` 里挂一列。
3. 在 `tests/test_core.py` 加一条断言（如值域/形状）。

### 新增一个信号

1. 在 `market_monitor/signals.py` 加 `_xxx_entry(cur, prev, ip, cost)`，返回 `SignalRecord` 或 `None`；用 `prev`（前一列）避免未来函数。
2. 在 `generate_signals` 的状态分支里调用。
3. 确认 `stop < close`、未跳过信号 `rr_estimate >= min_rr`。

### 新增一个数据源

1. 在 `market_monitor/data/sources.py` 继承 `Source`，实现 `fetch_daily` 返回 `index=date`、列 `open/high/low/close/volume` 的 DataFrame，失败抛 `SourceError`。
2. 在 `DataManager._source` 注册；在 `_resolve_source` 里给 `auto` 分支加推断规则。

## 常见问题

- **某标的「数据不足（<60 根），跳过」**：真实源历史不够或范围太短；调大 `--end` 前回看窗口（`data.start`）。
- **全部标的主源失败**：先 `python main.py --source synthetic` 验证流程，再查 token/代理/网络。
- **`state` 几乎全是 transition**：阈值偏严；调 `config.yaml` 的 `regime`（如 `adx_range`/`bw_range_pct`），并用回测对比。
