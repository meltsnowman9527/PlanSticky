"""账本数据层（清楚账本并入 PlanSticky 后的数据访问层）。

数据分三块：
    记账  transactions  + settings(budget)
    日记  journals      + 图片/视频文件（journal-images / journal-videos）
    签到  daily_checkins + checkin_tags

设计要点：
- **表结构与旧版 `F:\\work\\money\\app.py` 完全一致**（含列名、默认值、约束），
  这样 `ledger.db` 可以在两个程序之间直接互换，迁移只需拷贝文件。
- 所有写操作即时落库（= 自动保存），UI 不需要“保存”按钮（日记除外，见 journal_page）。
- 校验规则与旧版服务端逐条对齐，错误文案原样照搬，保证行为一致。
- WAL + busy_timeout：两个程序同时打开同一个库也不会互相锁死。

与 `database.py` 的分工：那个管计划便签的 tasks，这个管账本，两个库两个类。
"""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date as _date, datetime, timedelta
from typing import Optional

from plansticky import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT    NOT NULL,
    amount     REAL    NOT NULL CHECK(amount > 0),
    type       TEXT    NOT NULL DEFAULT 'expense',
    category   TEXT    NOT NULL,
    purpose    TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journals (
    date       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    format     TEXT NOT NULL DEFAULT 'text',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_checkins (
    date       TEXT PRIMARY KEY,
    activity   TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checkin_tags (
    name       TEXT PRIMARY KEY,
    color      TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# ---- 分类：名称 / 单字图标 / 颜色（与旧版 renderer/app.js 的 categories 一致）----
CATEGORIES: list[tuple[str, str, str]] = [
    ("餐饮", "餐", "#f59e0b"),
    ("交通", "行", "#3b82f6"),
    ("购物", "购", "#ec4899"),
    ("居住", "居", "#8b5cf6"),
    ("娱乐", "乐", "#14b8a6"),
    ("医疗", "医", "#ef4444"),
    ("学习", "学", "#6366f1"),
    ("其他", "其", "#64748b"),
]
CATEGORY_NAMES: list[str] = [c[0] for c in CATEGORIES]
CATEGORY_FALLBACK = "其他"

# ---- 默认签到标签（名称, 颜色）----
DEFAULT_CHECKIN_TAGS: list[tuple[str, str]] = [
    ("工作", "#2563eb"), ("学习", "#7c3aed"), ("运动", "#ea580c"),
    ("阅读", "#0f766e"), ("休息", "#64748b"), ("社交", "#db2777"),
    ("旅行", "#b45309"),
]
# 自定义标签按顺序轮转取色
CUSTOM_TAG_COLORS: list[str] = [
    "#0891b2", "#4f46e5", "#c2410c", "#15803d",
    "#be123c", "#9333ea", "#0369a1", "#a16207",
]

# ---- 长度限制（与旧版服务端校验一致）----
MAX_PURPOSE = 40
MAX_NOTE = 100
MAX_TAG_NAME = 10
MAX_MOOD = 200
MAX_JOURNAL_TEXT = 10000
MAX_IMAGE_SIZE = 20 * 1024 * 1024        # 20 MB（与旧版一致）
MAX_VIDEO_SIZE = 300 * 1024 * 1024       # 300 MB（与旧版一致）
DEFAULT_BUDGET = 5000.0

TYPE_EXPENSE = "expense"
TYPE_INCOME = "income"

# ---- 媒体文件名规则（与旧版服务端 IMAGE_NAME_RE / VIDEO_NAME_RE 一致）----
IMAGE_EXTS = (".png", ".jpg", ".webp", ".gif")
VIDEO_EXTS = (".mp4", ".webm", ".mov")
_MEDIA_NAME_RE = re.compile(
    r"^[0-9a-f]{32}\.(?:png|jpg|webp|gif|mp4|webm|mov)$", re.IGNORECASE)
_MEDIA_REF_RE = re.compile(
    r"/(?:journal-images|journal-videos)/(?P<name>[0-9a-f]{32}\."
    r"(?:png|jpg|webp|gif|mp4|webm|mov))", re.IGNORECASE)


def category_color(name: str) -> str:
    """分类色；未知分类回退到「其他」的颜色（与旧版前端回退行为一致）。"""
    for n, _icon, color in CATEGORIES:
        if n == name:
            return color
    return CATEGORIES[-1][2]


def category_icon(name: str) -> str:
    """分类单字图标；未知分类回退到「其他」。"""
    for n, icon, _color in CATEGORIES:
        if n == name:
            return icon
    return CATEGORIES[-1][1]


# ------------------------------------------------------------------ 数据类
@dataclass(frozen=True)
class Transaction:
    id: int
    date: str
    amount: float
    type: str
    category: str
    purpose: str
    note: str
    created_at: str = ""

    @property
    def is_income(self) -> bool:
        return self.type == TYPE_INCOME


@dataclass(frozen=True)
class Journal:
    date: str
    content: str
    format: str = "html"
    updated_at: str = ""


@dataclass(frozen=True)
class Checkin:
    date: str
    activity: str = ""
    detail: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class CheckinTag:
    name: str
    color: str


@dataclass(frozen=True)
class MonthSummary:
    """月度概览（口径与旧版一致，见 docs/融合规格.md 1.2）。"""
    month: str            # YYYY-MM
    expense: float        # 本月支出合计
    income: float         # 本月收入合计
    count: int            # 本月**支出**笔数（旧版含收入，此处修正）
    days: int             # 当月已过天数（非当月=整月天数）
    average: float        # expense / days
    highest: Optional[Transaction]  # 本月最大支出


class ValidationError(ValueError):
    """校验失败，message 即面向用户的错误文案。"""


def is_valid_date(day: str) -> bool:
    """严格校验 'YYYY-MM-DD'。

    注意：Python 的 `strptime("%Y-%m-%d")` 比旧版 JS 宽松（会接受 '2026-9-1'），
    这里额外要求 round-trip 一致，与旧版行为对齐。
    """
    text = str(day or "")
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d") == text
    except (ValueError, TypeError):
        return False


def normalize_space(text: str) -> str:
    """把连续空白压成单个空格并去首尾。

    **不能只用 `str.strip()`**：它不处理不换行空格 U+00A0（网页复制粘贴常见）
    和全角空格 U+3000，会让「只粘贴了一串空格」被误判成有内容。
    `split()` 按 Unicode 空白切分，能正确处理这些字符。
    """
    return " ".join(str(text or "").split())


# ------------------------------------------------------------------ 主类
class LedgerDatabase:
    def __init__(self, path: Optional[str] = None):
        # 路径解析顺序：显式参数 > 环境变量（测试隔离）> 数据目录默认
        self.path = path or os.environ.get("PLANSTICKY_LEDGER_DB") or config.ledger_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # ------------------------------------------------------------- 连接
    def _open(self) -> None:
        if self.path != ":memory:":
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        """首次创建时写入默认标签与设置（已存在则不动，避免覆盖用户改动）。"""
        c = self._conn_or_raise()
        c.executemany(
            "INSERT OR IGNORE INTO checkin_tags(name, color, sort_order) VALUES(?,?,?)",
            [(name, color, i) for i, (name, color) in enumerate(DEFAULT_CHECKIN_TAGS)],
        )
        c.executemany(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)",
            [("budget", str(int(DEFAULT_BUDGET))), ("theme", "light")],
        )
        c.commit()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def _conn_or_raise(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ledger database closed")
        return self._conn

    # ------------------------------------------------------------- 设置
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        c = self._conn_or_raise()
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        c = self._conn_or_raise()
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        c.commit()

    # 预算
    def budget(self) -> float:
        raw = self.get_setting(config.KEY_BUDGET)
        try:
            value = float(raw) if raw is not None else DEFAULT_BUDGET
        except (TypeError, ValueError):
            return DEFAULT_BUDGET
        return value if value > 0 else DEFAULT_BUDGET

    def set_budget(self, value: float) -> None:
        """预算最小为 1（与旧版 `max(1, Number(值)||5000)` 一致）。"""
        try:
            budget = float(value)
        except (TypeError, ValueError):
            budget = DEFAULT_BUDGET
        if budget < 1:
            budget = 1.0
        self.set_setting(config.KEY_BUDGET, str(round(budget, 2)))

    # ============================================================= 记账
    @staticmethod
    def validate_transaction(date: str, amount, tx_type: str,
                             category: str, purpose: str,
                             note: str = "") -> dict:
        """校验一笔账，通过返回规整后的字段字典，否则抛 ValidationError。

        文案与旧版 `app.py::validate` 逐条一致。
        """
        try:
            value = round(float(amount), 2)
        except (ValueError, TypeError):
            raise ValidationError("日期或金额格式无效") from None
        if not is_valid_date(date):
            raise ValidationError("日期或金额格式无效")

        category = str(category or "")
        purpose = str(purpose or "").strip()
        note = str(note or "").strip()
        tx_type = str(tx_type or TYPE_EXPENSE)

        if value <= 0:
            raise ValidationError("金额必须大于 0")
        if tx_type not in (TYPE_EXPENSE, TYPE_INCOME):
            raise ValidationError("类型无效")
        if category not in CATEGORY_NAMES:
            raise ValidationError("分类无效")
        if not purpose or len(purpose) > MAX_PURPOSE:
            raise ValidationError(f"用途必填且最多 {MAX_PURPOSE} 个字")
        if len(note) > MAX_NOTE:
            raise ValidationError(f"备注最多 {MAX_NOTE} 个字")

        return {"date": str(date), "amount": value, "type": tx_type,
                "category": category, "purpose": purpose, "note": note}

    def add_transaction(self, date: str, amount, tx_type: str = TYPE_EXPENSE,
                        category: str = "餐饮", purpose: str = "",
                        note: str = "") -> int:
        item = self.validate_transaction(date, amount, tx_type, category, purpose, note)
        c = self._conn_or_raise()
        cur = c.execute(
            "INSERT INTO transactions(date, amount, type, category, purpose, note) "
            "VALUES(:date, :amount, :type, :category, :purpose, :note)",
            item,
        )
        c.commit()
        return int(cur.lastrowid)

    def update_transaction(self, tx_id: int, date: str, amount, tx_type: str,
                           category: str, purpose: str, note: str = "") -> bool:
        item = self.validate_transaction(date, amount, tx_type, category, purpose, note)
        c = self._conn_or_raise()
        cur = c.execute(
            "UPDATE transactions SET date=:date, amount=:amount, type=:type, "
            "category=:category, purpose=:purpose, note=:note WHERE id=:id",
            {**item, "id": int(tx_id)},
        )
        c.commit()
        return bool(cur.rowcount)

    def delete_transaction(self, tx_id: int) -> bool:
        c = self._conn_or_raise()
        cur = c.execute("DELETE FROM transactions WHERE id = ?", (int(tx_id),))
        c.commit()
        return bool(cur.rowcount)

    def get_transaction(self, tx_id: int) -> Optional[Transaction]:
        c = self._conn_or_raise()
        row = c.execute("SELECT * FROM transactions WHERE id = ?", (int(tx_id),)).fetchone()
        return self._tx(row) if row else None

    @staticmethod
    def _tx(row: sqlite3.Row) -> Transaction:
        return Transaction(
            id=int(row["id"]), date=row["date"], amount=float(row["amount"]),
            type=row["type"], category=row["category"], purpose=row["purpose"],
            note=row["note"], created_at=row["created_at"] or "",
        )

    def list_transactions(self, month: Optional[str] = None,
                          date_from: Optional[str] = None,
                          date_to: Optional[str] = None,
                          category: Optional[str] = None,
                          tx_type: str = "all",
                          keyword: str = "") -> list[Transaction]:
        """筛选账目，规则与旧版前端 `filtered()` 完全一致：

        - 任一日期框非空 → 用日期范围（闭区间），**完全忽略 month**；
          否则只保留 date 以 month 开头的记录（month 为空则命中全部）。
        - tx_type='all' 不筛类型，category=None/'全部' 不筛分类。
        - 关键词匹配「用途 备注」（不区分大小写，先 trim）。
        - 排序：date DESC, id DESC。
        """
        c = self._conn_or_raise()
        where: list[str] = []
        params: list = []

        if date_from or date_to:
            if date_from:
                where.append("date >= ?")
                params.append(str(date_from))
            if date_to:
                where.append("date <= ?")
                params.append(str(date_to))
        elif month:
            where.append("date LIKE ?")
            params.append(f"{month}%")

        if tx_type in (TYPE_EXPENSE, TYPE_INCOME):
            where.append("type = ?")
            params.append(tx_type)
        if category and category != "全部":
            where.append("category = ?")
            params.append(category)

        keyword = (keyword or "").strip().lower()
        if keyword:
            where.append("LOWER(purpose || ' ' || note) LIKE ?")
            params.append(f"%{keyword}%")

        sql = "SELECT * FROM transactions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY date DESC, id DESC"
        return [self._tx(r) for r in c.execute(sql, params).fetchall()]

    # ---- 统计 ----
    def month_summary(self, month: Optional[str] = None) -> MonthSummary:
        """月度概览。month 为 'YYYY-MM'，空则用当前月。"""
        c = self._conn_or_raise()
        month = month or _date.today().strftime("%Y-%m")
        rows = c.execute(
            "SELECT * FROM transactions WHERE date LIKE ?", (f"{month}%",)
        ).fetchall()
        items = [self._tx(r) for r in rows]
        expenses = [t for t in items if not t.is_income]
        expense = round(sum(t.amount for t in expenses), 2)
        income = round(sum(t.amount for t in items if t.is_income), 2)
        highest = max(expenses, key=lambda t: t.amount) if expenses else None

        # 天数：当月=今天是几号；其它月=该月自然天数
        try:
            year, mon = int(month[:4]), int(month[5:7])
            today = _date.today()
            if (year, mon) == (today.year, today.month):
                days = today.day
            else:
                next_month = _date(year + (mon // 12), (mon % 12) + 1, 1)
                days = (next_month - timedelta(days=1)).day
        except (ValueError, IndexError):
            days = 0

        return MonthSummary(
            month=month, expense=expense, income=income,
            count=len(expenses), days=days,
            average=round(expense / days, 2) if days else 0.0,
            highest=highest,
        )

    def category_totals(self, month: Optional[str] = None) -> list[tuple[str, float]]:
        """当月各分类支出合计，降序，金额为 0 的分类剔除（供环形图）。"""
        month = month or _date.today().strftime("%Y-%m")
        c = self._conn_or_raise()
        rows = c.execute(
            "SELECT category, COALESCE(SUM(amount), 0) AS total FROM transactions "
            "WHERE date LIKE ? AND type != ? GROUP BY category",
            (f"{month}%", TYPE_INCOME),
        ).fetchall()
        totals = [(r["category"], round(float(r["total"]), 2)) for r in rows]
        totals = [t for t in totals if t[1] > 0]
        return sorted(totals, key=lambda t: t[1], reverse=True)

    def trend(self, days: int = 7, end: Optional[str] = None) -> list[tuple[str, float]]:
        """最近 days 天的每日支出合计（收入不计），升序，含无记录的 0 值天。

        最后一项是 end（默认今天），与旧版趋势图口径一致。
        """
        days = max(1, int(days))
        last = (datetime.strptime(end, "%Y-%m-%d").date() if end else _date.today())
        start = last - timedelta(days=days - 1)
        c = self._conn_or_raise()
        rows = c.execute(
            "SELECT date, COALESCE(SUM(amount), 0) AS total FROM transactions "
            "WHERE date BETWEEN ? AND ? AND type != ? GROUP BY date",
            (start.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"), TYPE_INCOME),
        ).fetchall()
        found = {r["date"]: round(float(r["total"]), 2) for r in rows}
        result = []
        for i in range(days):
            key = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            result.append((key, found.get(key, 0.0)))
        return result

    def transaction_count(self) -> int:
        c = self._conn_or_raise()
        return int(c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])

    def transaction_dates(self) -> list[str]:
        """有账目的所有日期（升序），供日历标记。"""
        c = self._conn_or_raise()
        rows = c.execute("SELECT DISTINCT date FROM transactions ORDER BY date").fetchall()
        return [r["date"] for r in rows]

    # ---- 批量导入（CSV / JSON 备份）----
    def import_transactions(self, items: list[dict]) -> tuple[int, int]:
        """批量追加导入，逐条校验：通过则插入，失败静默跳过（与旧版一致）。

        返回 (成功数, 跳过数)。
        """
        c = self._conn_or_raise()
        inserted = skipped = 0
        for raw in items or []:
            try:
                item = self.validate_transaction(
                    raw.get("date"), raw.get("amount"), raw.get("type", TYPE_EXPENSE),
                    raw.get("category", CATEGORY_FALLBACK),
                    raw.get("purpose", ""), raw.get("note", ""),
                )
            except (ValidationError, AttributeError, TypeError):
                skipped += 1
                continue
            c.execute(
                "INSERT INTO transactions(date, amount, type, category, purpose, note) "
                "VALUES(:date, :amount, :type, :category, :purpose, :note)",
                item,
            )
            inserted += 1
        c.commit()
        return inserted, skipped

    # ============================================================= 日记
    def get_journal(self, day: str) -> Optional[Journal]:
        c = self._conn_or_raise()
        row = c.execute("SELECT * FROM journals WHERE date = ?", (str(day),)).fetchone()
        if not row:
            return None
        return Journal(date=row["date"], content=row["content"],
                       format=row["format"], updated_at=row["updated_at"] or "")

    def save_journal(self, day: str, content: str, fmt: str = "html") -> None:
        """写入/覆盖某天日记。content 为旧版格式 HTML（由 journal_html 归一化产生）。"""
        if not is_valid_date(day):
            raise ValidationError("日记日期无效")
        if fmt not in ("html", "text"):
            raise ValidationError("日记格式无效")
        c = self._conn_or_raise()
        c.execute(
            "INSERT INTO journals(date, content, format, updated_at) "
            "VALUES(?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(date) DO UPDATE SET content = excluded.content, "
            "format = excluded.format, updated_at = CURRENT_TIMESTAMP",
            (str(day), str(content), fmt),
        )
        c.commit()

    def delete_journal(self, day: str) -> bool:
        c = self._conn_or_raise()
        cur = c.execute("DELETE FROM journals WHERE date = ?", (str(day),))
        c.commit()
        return bool(cur.rowcount)

    def list_journals(self) -> list[Journal]:
        """全部日记，按日期降序。"""
        c = self._conn_or_raise()
        rows = c.execute("SELECT * FROM journals ORDER BY date DESC").fetchall()
        return [Journal(date=r["date"], content=r["content"], format=r["format"],
                        updated_at=r["updated_at"] or "") for r in rows]

    def journal_count(self) -> int:
        c = self._conn_or_raise()
        return int(c.execute("SELECT COUNT(*) FROM journals").fetchone()[0])

    def journal_dates(self) -> set[str]:
        c = self._conn_or_raise()
        return {r["date"] for r in c.execute("SELECT date FROM journals").fetchall()}

    # ============================================================= 签到
    def get_checkin(self, day: str) -> Optional[Checkin]:
        c = self._conn_or_raise()
        row = c.execute("SELECT * FROM daily_checkins WHERE date = ?", (str(day),)).fetchone()
        if not row:
            return None
        return Checkin(date=row["date"], activity=row["activity"],
                       detail=row["detail"], updated_at=row["updated_at"] or "")

    def save_checkin(self, day: str, activity: str = "", detail: str = "") -> None:
        """写入/覆盖某天签到。校验规则与旧版 PATCH /api/checkins 一致。"""
        if not is_valid_date(day):
            raise ValidationError("签到日期无效")
        activity = str(activity or "")
        detail = str(detail or "").strip()
        if activity and not self.has_tag(activity):
            raise ValidationError("签到标签无效")
        # 用 normalize_space 判定“是否有内容”，否则只有 U+00A0 的输入会被存进库
        if not activity and not normalize_space(detail):
            raise ValidationError("请选择标签或填写今日心情")
        if len(detail) > MAX_MOOD:
            raise ValidationError(f"今日心情最多 {MAX_MOOD} 个字")
        c = self._conn_or_raise()
        c.execute(
            "INSERT INTO daily_checkins(date, activity, detail, updated_at) "
            "VALUES(?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(date) DO UPDATE SET activity = excluded.activity, "
            "detail = excluded.detail, updated_at = CURRENT_TIMESTAMP",
            (str(day), activity, detail),
        )
        c.commit()

    def delete_checkin(self, day: str) -> bool:
        c = self._conn_or_raise()
        cur = c.execute("DELETE FROM daily_checkins WHERE date = ?", (str(day),))
        c.commit()
        return bool(cur.rowcount)

    def list_checkins(self) -> list[Checkin]:
        c = self._conn_or_raise()
        rows = c.execute("SELECT * FROM daily_checkins ORDER BY date DESC").fetchall()
        return [Checkin(date=r["date"], activity=r["activity"], detail=r["detail"],
                        updated_at=r["updated_at"] or "") for r in rows]

    def checkins_in_month(self, month: str) -> dict[str, Checkin]:
        """某月 {'YYYY-MM-DD': Checkin}，供月历渲染。"""
        c = self._conn_or_raise()
        rows = c.execute(
            "SELECT * FROM daily_checkins WHERE date LIKE ?", (f"{month}%",)
        ).fetchall()
        return {r["date"]: Checkin(date=r["date"], activity=r["activity"],
                                   detail=r["detail"], updated_at=r["updated_at"] or "")
                for r in rows}

    def checkin_count_in_month(self, month: str) -> int:
        c = self._conn_or_raise()
        return int(c.execute("SELECT COUNT(*) FROM daily_checkins WHERE date LIKE ?",
                             (f"{month}%",)).fetchone()[0])

    # ---- 签到标签 ----
    def list_tags(self) -> list[CheckinTag]:
        c = self._conn_or_raise()
        rows = c.execute(
            "SELECT name, color FROM checkin_tags ORDER BY sort_order, name"
        ).fetchall()
        return [CheckinTag(name=r["name"], color=r["color"]) for r in rows]

    def tag_color(self, name: str) -> str:
        """标签色；找不到或未签到回退到默认绿（与旧版 `#176b4d` 回退一致）。"""
        c = self._conn_or_raise()
        row = c.execute("SELECT color FROM checkin_tags WHERE name = ?", (str(name),)).fetchone()
        return row["color"] if row else "#176b4d"

    def has_tag(self, name: str) -> bool:
        c = self._conn_or_raise()
        return c.execute("SELECT 1 FROM checkin_tags WHERE name = ?", (str(name),)).fetchone() is not None

    def add_tag(self, name: str) -> CheckinTag:
        """新增自定义标签，颜色按已有自定义标签数轮转（与旧版一致）。"""
        name = str(name or "").strip()
        if not name or len(name) > MAX_TAG_NAME:
            raise ValidationError(f"标签名称必填且最多 {MAX_TAG_NAME} 个字")
        if self.has_tag(name):
            raise ValidationError("标签已存在")
        c = self._conn_or_raise()
        defaults = tuple(n for n, _ in DEFAULT_CHECKIN_TAGS)
        placeholders = ",".join("?" * len(defaults))
        custom_count = int(c.execute(
            f"SELECT COUNT(*) FROM checkin_tags WHERE name NOT IN ({placeholders})",
            defaults,
        ).fetchone()[0])
        color = CUSTOM_TAG_COLORS[custom_count % len(CUSTOM_TAG_COLORS)]
        sort_order = int(c.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM checkin_tags"
        ).fetchone()[0])
        c.execute("INSERT INTO checkin_tags(name, color, sort_order) VALUES(?,?,?)",
                  (name, color, sort_order))
        c.commit()
        return CheckinTag(name=name, color=color)

    def delete_tag(self, name: str) -> bool:
        """删除标签（默认 7 个不允许删除）。同日签到里的该标签置空。"""
        if name in {n for n, _ in DEFAULT_CHECKIN_TAGS}:
            return False
        c = self._conn_or_raise()
        cur = c.execute("DELETE FROM checkin_tags WHERE name = ?", (str(name),))
        if cur.rowcount:
            c.execute("UPDATE daily_checkins SET activity = '' WHERE activity = ?", (str(name),))
        c.commit()
        return bool(cur.rowcount)

    # ============================================================= 媒体
    def save_journal_image(self, data: bytes, ext: str) -> str:
        """保存日记图片，返回旧版格式的相对 URL（/journal-images/xxx.jpg）。

        魔数校验由调用方（journal_page）负责，这里只管落盘。
        """
        ext = ext if ext.startswith(".") else f".{ext}"
        filename = f"{uuid.uuid4().hex}{ext}"
        target = os.path.join(config.journal_images_dir(), filename)
        with open(target, "wb") as fh:
            fh.write(data)
        return f"/journal-images/{filename}"

    def save_journal_video(self, data: bytes, ext: str) -> str:
        """保存日记视频（先写 .part 再原子改名），返回相对 URL。"""
        ext = ext if ext.startswith(".") else f".{ext}"
        filename = f"{uuid.uuid4().hex}{ext}"
        directory = config.journal_videos_dir()
        target = os.path.join(directory, filename)
        temporary = f"{target}.part"
        try:
            with open(temporary, "wb") as fh:
                fh.write(data)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
        return f"/journal-videos/{filename}"

    def referenced_media(self) -> set[str]:
        """所有日记正文引用到的媒体文件名（用于孤立文件清理）。

        **必须用精确的文件名规则匹配**（32 位 hex + 合法扩展名），不能扫字符白名单：
        文件名后面紧跟 `"`（结束引号），白名单如果没排除引号就会一路吃进 `alt=` 属性，
        拼出根本不存在的名称 —— 那样清理功能会误删全部媒体文件。
        """
        names: set[str] = set()
        for journal in self.list_journals():
            for match in _MEDIA_REF_RE.finditer(journal.content or ""):
                names.add(match.group("name"))
        return names

    def all_referenced_basenames(self) -> set[str]:
        """所有日记正文里出现过的「媒体文件名」，用宽松方式提取（安全侧）。

        与 `referenced_media()` 的区别：这里只排除明显危险的名称，其余一律视为
        「可能被引用」。清理孤立文件时以它为准 —— 宁可漏删（留下垃圾），
        绝不能误删用户多年的照片和视频。
        """
        names: set[str] = set()
        for journal in self.list_journals():
            content = journal.content or ""
            for token in ("/journal-images/", "/journal-videos/"):
                start = 0
                while True:
                    index = content.find(token, start)
                    if index < 0:
                        break
                    rest = content[index + len(token):]
                    # 媒体名不可能含空白、引号、斜杠、尖括号；截断到 128 字符防畸形
                    candidate = rest.split('"', 1)[0]
                    candidate = candidate.split("'", 1)[0]
                    candidate = candidate.split(">", 1)[0]
                    candidate = candidate.split("<", 1)[0]
                    candidate = candidate.split("?", 1)[0]
                    candidate = candidate.split("#", 1)[0]
                    candidate = candidate.strip()[:128]
                    base = os.path.basename(candidate)
                    if base and base not in (".", "..") and "\\" not in base:
                        names.add(base)
                    start = index + len(token)
        return names

    def cleanup_orphan_media(self, dry_run: bool = False) -> list[str]:
        """删除没有任何日记引用的图片/视频，返回被删文件名列表。

        修正旧版缺陷：旧版媒体先上传后保存，不保存就留下永久垃圾文件。

        安全设计（防止把用户照片删光）：
        - 引用集合取 `all_referenced_basenames()` 的**并集**（宽松，宁可漏删）；
        - 只删符合媒体命名规则（32 位 hex + 白名单扩展名）的文件；
        - 只删普通文件，不递归、不跟随符号链接。
        """
        referenced = self.referenced_media() | self.all_referenced_basenames()
        removed: list[str] = []
        for directory in (config.journal_images_dir(), config.journal_videos_dir()):
            if not os.path.isdir(directory):
                continue
            for name in sorted(os.listdir(directory)):
                if name in referenced or not _MEDIA_NAME_RE.fullmatch(name):
                    continue
                if name.endswith(".part"):
                    continue
                target = os.path.join(directory, name)
                if not os.path.isfile(target) or os.path.islink(target):
                    continue
                removed.append(name)
                if not dry_run:
                    try:
                        os.remove(target)
                    except OSError:
                        removed.pop()
        return removed
