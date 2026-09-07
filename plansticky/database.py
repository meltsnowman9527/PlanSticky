"""SQLite 数据层。

设计要点：
- 所有写操作即时落库（= 自动保存），UI 不需要“保存”按钮。
- 任务表 tasks 用 kind 区分长期/短期：
      kind='long'  -> day 恒为 NULL，全窗口共享一份
      kind='day'   -> day 为 'YYYY-MM-DD'，每天独立
- 排序：同一“区域”（长期计划 或 某一天的短期计划）内用连续整数 pos。
  界面显示时永远“未完成在上、已完成在下”，分区内部按 pos 排序；
  因此 pos 只表达分区内部的相对顺序，跨区移动不影响另一方。
- settings 表以 key-value 存窗口几何、置顶、主题等 UI 状态。

崩溃保护：启用 WAL 日志 + busy_timeout，普通断电/杀进程不会损坏数据。
"""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL CHECK (kind IN ('long','day')),
    day        TEXT,
    content    TEXT    NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    pos        INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_region ON tasks(kind, day);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

KIND_LONG = "long"
KIND_DAY = "day"


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # ------------------------------------------------------------- 连接
    def _open(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def _conn_or_raise(self) -> sqlite3.Connection:
        if self._conn is None:  # close() 之后再写属于编程错误，直接暴露
            raise RuntimeError("database closed")
        return self._conn

    # ------------------------------------------------------- 区域 SQL 帮助
    @staticmethod
    def _region_where(kind: str, day: Optional[str]) -> tuple[str, list]:
        """kind/date -> (WHERE 子句, 参数)。day=None 对应 kind='long'。"""
        if kind == KIND_LONG:
            return "kind = 'long' AND day IS NULL", []
        return "kind = 'day' AND day = ?", [day]

    # ----------------------------------------------------------- settings
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        c = self._conn_or_raise()
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        c = self._conn_or_raise()
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        c.commit()

    def get_setting_bool(self, key: str, default: bool = False) -> bool:
        v = self.get_setting(key)
        if v is None:
            return default
        return v == "1"

    def set_setting_bool(self, key: str, value: bool) -> None:
        self.set_setting(key, "1" if value else "0")

    # 窗口几何记忆（单条 JSON 字符串，简单可靠）
    def get_geometry(self) -> Optional[dict]:
        v = self.get_setting("window_geometry")
        if not v:
            return None
        try:
            import json
            d = json.loads(v)
            if all(k in d for k in ("x", "y", "w", "h")):
                return d
        except (ValueError, TypeError):
            return None
        return None

    def save_geometry(self, x: int, y: int, w: int, h: int) -> None:
        import json
        self.set_setting("window_geometry", json.dumps({"x": int(x), "y": int(y), "w": int(w), "h": int(h)}))

    # ------------------------------------------------------------- 任务
    def add_task(self, kind: str, content: str, day: Optional[str] = None) -> int:
        """新增任务，追加到该区域末尾。返回新任务 id。"""
        c = self._conn_or_raise()
        where, params = self._region_where(kind, day)
        row = c.execute(
            f"SELECT COALESCE(MAX(pos), -1) AS m FROM tasks WHERE {where}", params
        ).fetchone()
        pos = row["m"] + 1
        cur = c.execute(
            "INSERT INTO tasks(kind, day, content, done, pos) VALUES(?, ?, ?, 0, ?)",
            (kind, day, content, pos),
        )
        c.commit()
        return int(cur.lastrowid)

    def update_content(self, task_id: int, content: str) -> None:
        c = self._conn_or_raise()
        c.execute(
            "UPDATE tasks SET content = ?, updated_at = datetime('now','localtime') "
            "WHERE id = ?",
            (content, task_id),
        )
        c.commit()

    def set_done(self, task_id: int, done: bool) -> None:
        c = self._conn_or_raise()
        c.execute(
            "UPDATE tasks SET done = ?, updated_at = datetime('now','localtime') "
            "WHERE id = ?",
            (1 if done else 0, task_id),
        )
        c.commit()

    def delete_task(self, task_id: int) -> None:
        c = self._conn_or_raise()
        c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        c.commit()

    def list_tasks(self, kind: str, day: Optional[str] = None) -> list[dict]:
        """某区域全部任务，按 pos 升序。由 UI 层负责分区显示。"""
        c = self._conn_or_raise()
        where, params = self._region_where(kind, day)
        rows = c.execute(
            f"SELECT id, kind, day, content, done, pos FROM tasks WHERE {where} "
            "ORDER BY pos ASC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def reorder(self, kind: str, day: Optional[str], ordered_ids: list[int]) -> None:
        """按新顺序重排分区。

        只关心“被拖动的那个分区块”（未完成或已完成）内部顺序：
        先给 ordered_ids 依次分配 0..n，其余任务保留原相对顺序接着排，
        保证另一分区块的内部顺序不被破坏。
        """
        c = self._conn_or_raise()
        where, params = self._region_where(kind, day)
        rows = c.execute(
            f"SELECT id, pos FROM tasks WHERE {where}", params
        ).fetchall()
        pos_map = {r["id"]: r["pos"] for r in rows}
        known = [i for i in ordered_ids if i in pos_map]
        rest = sorted((i for i in pos_map if i not in known), key=pos_map.get)
        updates = []
        for idx, task_id in enumerate(known + rest):
            updates.append((idx, task_id))
        c.executemany("UPDATE tasks SET pos = ? WHERE id = ?", updates)
        c.commit()

    def counts(self, kind: str, day: Optional[str] = None) -> tuple[int, int]:
        """返回 (已完成数, 总数)。"""
        c = self._conn_or_raise()
        where, params = self._region_where(kind, day)
        row = c.execute(
            f"SELECT COUNT(*) AS total, "
            f"COALESCE(SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END), 0) AS done "
            f"FROM tasks WHERE {where}",
            params,
        ).fetchone()
        return int(row["done"]), int(row["total"])

    def days_with_tasks(self) -> list[str]:
        """有短期任务的所有日期（升序），供“多日列表”视图使用。"""
        c = self._conn_or_raise()
        rows = c.execute(
            "SELECT DISTINCT day FROM tasks WHERE kind = 'day' AND day IS NOT NULL"
        ).fetchall()
        return sorted(r["day"] for r in rows)

    def day_stats_range(self, start: str, end: str) -> dict[str, tuple[int, int]]:
        """[start, end]（含两端）内每一天的 (已完成数, 总数)，供贡献图使用。"""
        c = self._conn_or_raise()
        rows = c.execute(
            "SELECT day, "
            "COALESCE(SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END), 0) AS done, "
            "COUNT(*) AS total "
            "FROM tasks WHERE kind = 'day' AND day BETWEEN ? AND ? "
            "GROUP BY day",
            (start, end),
        ).fetchall()
        return {r["day"]: (int(r["done"]), int(r["total"])) for r in rows}
