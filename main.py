"""入口：python main.py --end YYYYMMDD [--report daily|weekly] [--source synthetic]。

示例：
    python main.py --source synthetic --end 20260826 --report daily   # 离线演示日报
    python main.py --source synthetic --report weekly                 # 离线演示周报
    python main.py --report daily                                     # 真实数据（需 tushare token / yfinance）
"""
import sys

from market_monitor.cli import main

if __name__ == "__main__":
    sys.exit(main())
