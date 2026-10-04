"""路径与常量配置。

数据目录解析顺序：
    1. 环境变量 PLANSTICKY_DATA_DIR（测试/便携使用）
    2. %APPDATA%\\PlanSticky（默认，正式使用）
    3. 找不到 APPDATA 时回退 ~/.plansticky
"""
from __future__ import annotations

import os
import sys

APP_ID = "PlanSticky"                      # 单实例 / AppUserModelID 用（ASCII）
APP_DISPLAY_NAME = "计划便签"               # 界面/托盘显示名
APP_VERSION = "1.1.0"                      # 1.1.0 = 并入记账/日记/签到

SINGLE_INSTANCE_KEY = "plansticky_single_instance"

# 数据/UI 设置里用到的 key（字符串常量集中管理，避免散落各处）
KEY_THEME = "theme"                        # light | dark | system
KEY_GEOMETRY = "window_geometry"           # x,y,w,h
KEY_PINNED = "window_pinned"               # 1 | 0
KEY_LAST_TAB = "last_tab"                  # day | long | ledger | journal | checkin
KEY_DAY_MODE = "day_mode"                  # single | list | heat（短期页视图）
KEY_HIDE_DONE = "hide_done_long"           # 1 | 0

# ---- 账本（清楚账本并入）相关 ----
KEY_LEDGER_TAB = "ledger_tab"              # detail | charts（记账页二级视图）
KEY_MONEY_IMPORTED = "money_imported"      # 1 = 旧数据已导入过（幂等守卫）
KEY_TX_DRAFT = "tx_draft"                  # 记账表单未提交的草稿（JSON）
KEY_JOURNAL_DRAFT = "journal_draft"        # 日记未保存草稿（旧的 HTML）
KEY_BUDGET = "budget"                      # 月预算（字符串数字）


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """程序所在目录（源码运行时=项目根，exe 运行时=exe 所在目录）。"""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """数据目录（数据库/log 都放这里），不存在则创建。"""
    override = os.environ.get("PLANSTICKY_DATA_DIR")
    if override:
        base = override
    elif os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], APP_ID)
    else:
        base = os.path.join(os.path.expanduser("~"), "." + APP_ID.lower())
    os.makedirs(base, exist_ok=True)
    return base


def db_path() -> str:
    return os.path.join(data_dir(), "plans.db")


def ledger_db_path() -> str:
    """账本数据库（记账/日记/签到）。与 plans.db 分开，互不影响。"""
    return os.path.join(data_dir(), "ledger.db")


def journal_images_dir() -> str:
    """日记图片目录（不存在则创建）。"""
    path = os.path.join(data_dir(), "journal-images")
    os.makedirs(path, exist_ok=True)
    return path


def journal_videos_dir() -> str:
    """日记视频目录（不存在则创建）。"""
    path = os.path.join(data_dir(), "journal-videos")
    os.makedirs(path, exist_ok=True)
    return path


def log_path() -> str:
    return os.path.join(data_dir(), "error.log")
