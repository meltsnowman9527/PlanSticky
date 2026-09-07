#!/usr/bin/env python3
"""计划便签启动入口。

运行：  .venv\\Scripts\\python main.py
打包：  pyinstaller --onefile --windowed --name PlanSticky main.py
"""
import sys

from plansticky.app import main

if __name__ == "__main__":
    raise SystemExit(main())
