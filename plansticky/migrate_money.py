"""把「清楚账本」（F:\\work\\money）的历史数据一次性导入 PlanSticky。

原则：**只拷贝，绝不移动/修改源目录**。原程序可以继续用，源数据是备份。

    源                                      目标
    F:\\work\\money\\data\\ledger.db      ->  %APPDATA%\\PlanSticky\\ledger.db
    F:\\work\\money\\data\\journal-images ->  %APPDATA%\\PlanSticky\\journal-images
    F:\\work\\money\\data\\journal-videos ->  %APPDATA%\\PlanSticky\\journal-videos

两个实现细节：

1. **数据库用 sqlite3 在线备份 API（`Connection.backup`），不是 shutil.copy2。**
   旧库是 WAL 模式：最近的写入可能还在 `ledger.db-wal` 里没落盘，直接拷 .db
   会丢数据；而且旧程序若正在运行，copy 可能拿到半截状态。backup() 会读一份
   一致快照，且不阻塞对方。
2. **幂等**：导入完成写 `settings.money_imported=1`，之后自动跳过；媒体文件按
   文件名去重，重复执行不会翻倍。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

from plansticky import config

# 旧程序默认位置（用户可覆盖）
DEFAULT_MONEY_DIR = r"F:\work\money"

ProgressFn = Callable[[str, int, int], None]


@dataclass
class MigrationReport:
    """迁移结果，供 UI 展示或测试断言。"""
    skipped: bool = False                      # 已经导入过，直接跳过
    reason: str = ""                           # 跳过/失败原因
    db_copied: bool = False
    transactions: int = 0
    journals: int = 0
    checkins: int = 0
    tags: int = 0
    images_copied: int = 0
    videos_copied: int = 0
    media_skipped: int = 0                     # 目标已存在，跳过
    media_failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.skipped:
            return f"无需导入（{self.reason}）"
        return (f"导入完成：账目 {self.transactions} 笔、日记 {self.journals} 篇、"
                f"签到 {self.checkins} 条、标签 {self.tags} 个、"
                f"图片 {self.images_copied} 张、视频 {self.videos_copied} 个"
                + (f"，{len(self.media_failed)} 个媒体文件失败" if self.media_failed else ""))


def money_dir() -> str:
    """旧程序目录，可用环境变量 PLANSTICKY_MONEY_DIR 覆盖（测试用）。"""
    return os.environ.get("PLANSTICKY_MONEY_DIR", DEFAULT_MONEY_DIR)


def source_db_path() -> str:
    return os.path.join(money_dir(), "data", "ledger.db")


def already_imported() -> bool:
    """目标库里是否已标记导入过。目标库不存在时返回 False。"""
    target = config.ledger_db_path()
    if not os.path.exists(target):
        return False
    try:
        conn = sqlite3.connect(target, timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (config.KEY_MONEY_IMPORTED,),
            ).fetchone()
            return bool(row and str(row[0]) == "1")
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _copy_database(source: str, target: str) -> bool:
    """用 SQLite 在线备份把 source 完整复制到 target（覆盖）。

    返回是否成功。源库损坏/不是 SQLite 文件时返回 False 而不是抛异常。
    """
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        # 确认是可读的 SQLite 库
        try:
            src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            return False
        dst = sqlite3.connect(target, timeout=10)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return True


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0


def _copy_media(source_dir: str, target_dir: str,
                report: MigrationReport, on_progress: Optional[ProgressFn],
                label: str) -> None:
    """把媒体文件逐个拷进目标目录；已存在同名文件则跳过。"""
    if not os.path.isdir(source_dir):
        return
    names = [n for n in sorted(os.listdir(source_dir))
             if not n.endswith(".part") and os.path.isfile(os.path.join(source_dir, n))]
    os.makedirs(target_dir, exist_ok=True)
    for index, name in enumerate(names, start=1):
        target = os.path.join(target_dir, name)
        if os.path.exists(target):
            report.media_skipped += 1
        else:
            try:
                shutil.copy2(os.path.join(source_dir, name), target)
                if label == "image":
                    report.images_copied += 1
                else:
                    report.videos_copied += 1
            except OSError:
                report.media_failed.append(name)
        if on_progress is not None:
            on_progress(label, index, len(names))


def import_money_data(source_dir: Optional[str] = None,
                      force: bool = False,
                      on_progress: Optional[ProgressFn] = None) -> MigrationReport:
    """把旧程序数据导入 PlanSticky 数据目录。

    source_dir  旧程序根目录，默认 `F:\\work\\money` 或 `PLANSTICKY_MONEY_DIR`
    force       True 时忽略「已导入」标记，重新导入（媒体按同名去重，不会翻倍）
    on_progress 可选回调 (kind, done, total)，kind ∈ {'image','video'}
    """
    report = MigrationReport()
    root = source_dir or money_dir()
    source_db = os.path.join(root, "data", "ledger.db")

    if not os.path.isfile(source_db):
        report.skipped = True
        report.reason = f"找不到旧数据库：{source_db}"
        return report

    if not force and already_imported():
        report.skipped = True
        report.reason = "旧数据已导入过"
        return report

    target_db = config.ledger_db_path()
    if not _copy_database(source_db, target_db):
        report.skipped = True
        report.reason = f"旧数据库无法读取：{source_db}"
        return report
    report.db_copied = True

    # 统计导入内容（读目标库，与旧版表结构一致）
    conn = sqlite3.connect(target_db, timeout=10)
    try:
        report.transactions = _count_rows(conn, "transactions")
        report.journals = _count_rows(conn, "journals")
        report.checkins = _count_rows(conn, "daily_checkins")
        report.tags = _count_rows(conn, "checkin_tags")
    finally:
        conn.close()

    # 媒体文件（数据库好了再拷，先库后媒体：中断也能靠标记续跑）
    _copy_media(os.path.join(root, "data", "journal-images"),
                config.journal_images_dir(), report, on_progress, "image")
    _copy_media(os.path.join(root, "data", "journal-videos"),
                config.journal_videos_dir(), report, on_progress, "video")

    # 写入导入标记（用 LedgerDatabase 保证 settings 表存在且写法一致）
    from plansticky.ledger_db import LedgerDatabase
    db = LedgerDatabase(target_db)
    try:
        db.set_setting(config.KEY_MONEY_IMPORTED, "1")
        db.set_setting("money_import_source", root)
    finally:
        db.close()

    return report


def offer_import_on_startup() -> Optional[str]:
    """启动时的迁移决策。

    返回 None 表示无需导入；否则返回要展示给用户的提示文案。
    """
    if already_imported():
        return None
    if not os.path.isfile(source_db_path()):
        return None
    report = import_money_data()
    return report.summary()
