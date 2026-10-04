"""账本数据层 + 迁移 + 日记 HTML 归一化的离屏测试。

运行：  .venv\\Scripts\\python.exe tests\\ledger_test.py

特点：
- 全程在临时目录里跑，**绝不触碰真实数据**（`%APPDATA%\\PlanSticky` 与
  `F:\\work\\money` 都只读、只可选验证）。
- 自动构造一个「旧版 schema」的账本库作为迁移源，因此不依赖开发机上的
  `F:\\work\\money` 是否存在，任何机器上都能跑。
- 若检测到真实旧库，额外跑一组「真实数据只读验证」。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="plansticky_ledger_")
os.environ["PLANSTICKY_DATA_DIR"] = os.path.join(_TMP, "appdata")
os.environ["PLANSTICKY_MONEY_DIR"] = os.path.join(_TMP, "money")
os.environ["PLANSTICKY_SINGLE"] = "0"

from plansticky import config                                    # noqa: E402
from plansticky.ledger_db import (CATEGORY_NAMES, DEFAULT_BUDGET,  # noqa: E402
                                  Checkin, Journal, LedgerDatabase,
                                  Transaction, ValidationError,
                                  is_valid_date, normalize_space)
from plansticky.migrate_money import (already_imported,          # noqa: E402
                                     import_money_data)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def expect_error(name: str, fn, message: str) -> None:
    """断言 fn() 抛出 ValidationError 且文案完全一致（文案要照搬旧版）。"""
    try:
        fn()
    except ValidationError as exc:
        check(name, str(exc) == message, f"got {str(exc)!r} want {message!r}")
        return
    except Exception as exc:                        # noqa: BLE001
        check(name, False, f"raised {type(exc).__name__}: {exc}")
        return
    check(name, False, "没有抛异常")


# ==================================================================== 造源库
LEGACY_SCHEMA = """
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0), type TEXT NOT NULL DEFAULT 'expense',
    category TEXT NOT NULL, purpose TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE journals (date TEXT PRIMARY KEY, content TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'text', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE daily_checkins (date TEXT PRIMARY KEY, activity TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE checkin_tags (name TEXT PRIMARY KEY, color TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""

# 两个真实存在过的媒体文件名（迁移时要被拷过来）
IMG_A = "a" * 32 + ".jpg"
VID_B = "b" * 32 + ".mp4"
# 一个「传了但没保存」的孤立文件
IMG_ORPHAN = "c" * 32 + ".png"


def build_legacy_source() -> str:
    """在临时目录里造一个旧版结构的账本库 + 媒体文件，返回旧程序根目录。"""
    root = os.environ["PLANSTICKY_MONEY_DIR"]
    data = os.path.join(root, "data")
    images = os.path.join(data, "journal-images")
    videos = os.path.join(data, "journal-videos")
    for path in (images, videos):
        os.makedirs(path, exist_ok=True)

    conn = sqlite3.connect(os.path.join(data, "ledger.db"))
    conn.executescript(LEGACY_SCHEMA)
    conn.executemany(
        "INSERT INTO transactions(date, amount, type, category, purpose, note) "
        "VALUES(?,?,?,?,?,?)",
        [
            ("2026-08-01", 28.5, "expense", "餐饮", "午餐", "公司楼下"),
            ("2026-08-01", 12.0, "expense", "交通", "地铁", ""),
            ("2026-08-02", 8000.0, "income", "其他", "工资", "8 月"),
            ("2026-08-03", 264.0, "expense", "交通", "高铁票", "回家"),
            ("2026-07-15", 99.0, "expense", "娱乐", "电影", "imax"),
        ],
    )
    conn.executemany("INSERT INTO settings(key, value) VALUES(?,?)",
                     [("budget", "3500"), ("theme", "light")])
    conn.executemany(
        "INSERT INTO journals(date, content, format) VALUES(?,?,?)",
        [
            ("2026-08-01", f'<div>今天不错<br></div><div><img src="/journal-images/{IMG_A}" '
                           f'alt="午餐"></div>', "html"),
            ("2026-08-02", f'<div>有视频</div><div><video src="/journal-videos/{VID_B}" '
                           f'controls preload="metadata"></video></div>', "html"),
            ("2026-08-03", "纯文本日记", "text"),
        ],
    )
    conn.executemany(
        "INSERT INTO daily_checkins(date, activity, detail) VALUES(?,?,?)",
        [("2026-08-01", "工作", "还行"), ("2026-08-02", "运动", "跑步 5km")],
    )
    conn.executemany(
        "INSERT INTO checkin_tags(name, color, sort_order) VALUES(?,?,?)",
        [("工作", "#2563eb", 0), ("学习", "#7c3aed", 1), ("运动", "#ea580c", 2),
         ("阅读", "#0f766e", 3), ("休息", "#64748b", 4), ("社交", "#db2777", 5),
         ("旅行", "#b45309", 6)],
    )
    conn.commit()
    conn.close()

    # 媒体：两个被引用 + 一个孤立
    with open(os.path.join(images, IMG_A), "wb") as fh:
        fh.write(b"\xff\xd8\xff" + b"0" * 64)
    with open(os.path.join(videos, VID_B), "wb") as fh:
        fh.write(b"\x00\x00\x00\x20ftypisom" + b"0" * 64)
    with open(os.path.join(images, IMG_ORPHAN), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    # 故意留一个 WAL 尾巴：验证迁移用 backup() 而不是拷文件
    conn = sqlite3.connect(os.path.join(data, "ledger.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO transactions(date, amount, type, category, purpose) "
                 "VALUES('2026-08-04', 55.5, 'expense', '购物', 'WAL 里的记录')")
    conn.commit()
    # 不 close：让 -wal 文件留在磁盘上，模拟“旧程序正在运行”
    return root


# ============================================================== 数据层测试
def test_validation(db_path: str) -> None:
    db = LedgerDatabase(db_path)
    print("\n---- 校验规则（文案须与旧版一致）----")
    expect_error("日期格式非法被拒",
                 lambda: db.add_transaction("2026-9-1", 10, "expense", "餐饮", "x"),
                 "日期或金额格式无效")
    expect_error("金额为 0 被拒",
                 lambda: db.add_transaction("2026-09-01", 0, "expense", "餐饮", "x"),
                 "金额必须大于 0")
    expect_error("负数金额被拒",
                 lambda: db.add_transaction("2026-09-01", -5, "expense", "餐饮", "x"),
                 "金额必须大于 0")
    expect_error("类型非法被拒",
                 lambda: db.add_transaction("2026-09-01", 10, "refund", "餐饮", "x"),
                 "类型无效")
    expect_error("分类非法被拒",
                 lambda: db.add_transaction("2026-09-01", 10, "expense", "乱写", "x"),
                 "分类无效")
    expect_error("用途为空被拒",
                 lambda: db.add_transaction("2026-09-01", 10, "expense", "餐饮", "   "),
                 "用途必填且最多 40 个字")
    expect_error("用途超长被拒",
                 lambda: db.add_transaction("2026-09-01", 10, "expense", "餐饮", "字" * 41),
                 "用途必填且最多 40 个字")
    expect_error("备注超长被拒",
                 lambda: db.add_transaction("2026-09-01", 10, "expense", "餐饮", "x", "字" * 101),
                 "备注最多 100 个字")

    check("日期严格性：2026-9-1 不合法", not is_valid_date("2026-9-1"))
    check("日期严格性：2026-09-01 合法", is_valid_date("2026-09-01"))
    check("日期严格性：2026-02-30 不合法", not is_valid_date("2026-02-30"))

    # 边界：刚好 40 / 100 字应通过
    tx_id = db.add_transaction("2026-09-01", 10, "expense", "餐饮", "字" * 40, "字" * 100)
    check("边界值 40 字用途 / 100 字备注 通过", db.get_transaction(tx_id) is not None)
    db.close()


def test_transactions(db_path: str) -> None:
    db = LedgerDatabase(db_path)
    print("\n---- 记账增删改查 ----")
    a = db.add_transaction("2026-05-01", 28.5, "expense", "餐饮", "午餐", "楼下")
    b = db.add_transaction("2026-05-01", 12.0, "expense", "交通", "地铁")
    c = db.add_transaction("2026-05-02", 9000.0, "income", "其他", "工资")
    d = db.add_transaction("2026-04-20", 300.0, "expense", "购物", "鞋")
    check("新增返回自增 id", len({a, b, c, d}) == 4)

    rows = db.list_transactions(month="2026-05")
    check("按月筛选命中 3 笔", len(rows) == 3, str(len(rows)))
    check("排序 date DESC, id DESC",
          [r.id for r in rows] == [c, b, a], str([r.id for r in rows]))

    got = db.get_transaction(a)
    check("get_transaction 字段完整",
          isinstance(got, Transaction) and got.amount == 28.5 and got.purpose == "午餐"
          and got.is_income is False)

    check("update 生效",
          db.update_transaction(a, "2026-05-03", 30.0, "expense", "餐饮", "午餐改", "n")
          and db.get_transaction(a).amount == 30.0
          and db.get_transaction(a).date == "2026-05-03")
    check("update 不存在的 id 返回 False",
          db.update_transaction(999999, "2026-05-03", 1, "expense", "餐饮", "x") is False)
    check("delete 生效", db.delete_transaction(b) and db.get_transaction(b) is None)
    check("delete 不存在的 id 返回 False", db.delete_transaction(999999) is False)

    # 此刻 5 月剩 3 笔：30.0 支出(改过日期)、9000 收入、66.0 支出；另有 4 月 300.0 支出
    # 筛选组合（规则对齐旧版 filtered()）
    db.add_transaction("2026-05-04", 66.0, "expense", "娱乐", "电影", "imax")
    check("类型筛选 income",
          [r.purpose for r in db.list_transactions(month="2026-05", tx_type="income")] == ["工资"])
    check("类型筛选 expense 命中 2 笔（地铁那笔已删）",
          len(db.list_transactions(month="2026-05", tx_type="expense")) == 2,
          str(len(db.list_transactions(month="2026-05", tx_type="expense"))))
    check("分类筛选",
          [r.purpose for r in db.list_transactions(month="2026-05", category="娱乐")] == ["电影"])
    check("分类「全部」等于不过滤",
          len(db.list_transactions(month="2026-05", category="全部")) == 3,
          str(len(db.list_transactions(month="2026-05", category="全部"))))
    check("关键词匹配用途",
          [r.purpose for r in db.list_transactions(keyword="地铁")] == [])
    check("关键词匹配备注",
          [r.purpose for r in db.list_transactions(keyword="imax")] == ["电影"])
    check("关键词大小写不敏感",
          [r.purpose for r in db.list_transactions(keyword="IMAX")] == ["电影"])
    check("日期范围优先于月份（含边界）",
          len(db.list_transactions(month="2026-01",
                                   date_from="2026-05-03", date_to="2026-05-04")) == 2)
    check("只给起始日期",
          len(db.list_transactions(date_from="2026-05-04")) == 1)
    check("只给结束日期",
          len(db.list_transactions(date_to="2026-04-30")) == 1)
    check("无任何筛选返回全部", len(db.list_transactions()) == 4,
          str(len(db.list_transactions())))

    print("\n---- 统计口径 ----")
    summary = db.month_summary("2026-05")
    check("月支出合计（不含收入）", summary.expense == 96.0, str(summary.expense))
    check("月收入合计", summary.income == 9000.0, str(summary.income))
    check("笔数只算支出（修正旧版含收入的 bug）", summary.count == 2, str(summary.count))
    check("非当月天数=整月天数", summary.days == 31, str(summary.days))
    check("日均 = 支出 / 天数", summary.average == round(96.0 / 31, 2), str(summary.average))
    check("最高单笔取支出最大", summary.highest is not None
          and summary.highest.amount == 66.0, str(summary.highest))

    today_month = __import__("datetime").date.today().strftime("%Y-%m")
    same = db.month_summary(today_month)
    check("当月天数=今天几号",
          same.days == __import__("datetime").date.today().day, str(same.days))

    totals = db.category_totals("2026-05")
    check("分类合计只含支出且降序",
          [t[0] for t in totals] == ["娱乐", "餐饮"], str(totals))
    check("分类合计金额正确", totals[0][1] == 66.0, str(totals))
    check("无数据月份分类合计为空", db.category_totals("2020-01") == [])

    trend = db.trend(3, end="2026-05-04")
    check("趋势长度 = 天数", len(trend) == 3, str(len(trend)))
    check("趋势末日是 end", trend[-1][0] == "2026-05-04", str(trend[-1]))
    check("趋势首日正确", trend[0][0] == "2026-05-02", str(trend[0]))
    check("趋势只算支出",
          [v for _d, v in trend] == [0.0, 30.0, 66.0], str(trend))
    check("趋势含无记录天（0 值）", trend[0][1] == 0.0)
    check("趋势可指定天数", len(db.trend(90, end="2026-05-04")) == 90)

    check("transaction_count", db.transaction_count() == 4, str(db.transaction_count()))
    check("transaction_dates 升序去重",
          db.transaction_dates() == ["2026-04-20", "2026-05-02", "2026-05-03", "2026-05-04"],
          str(db.transaction_dates()))

    print("\n---- 批量导入 ----")
    inserted, skipped = db.import_transactions([
        {"date": "2026-06-01", "amount": 5, "type": "expense", "category": "餐饮", "purpose": "早饭"},
        {"date": "2026-06-01", "amount": 7, "type": "expense", "category": "交通", "purpose": "公交"},
        {"date": "坏日期", "amount": 7, "type": "expense", "category": "餐饮", "purpose": "坏"},
        {"date": "2026-06-01", "amount": -1, "type": "expense", "category": "餐饮", "purpose": "负"},
        {"date": "2026-06-01", "amount": 9, "type": "expense", "category": "没这分类", "purpose": "x"},
    ])
    check("导入通过 2 条", inserted == 2, str(inserted))
    check("导入静默跳过 3 条", skipped == 3, str(skipped))
    check("导入结果落库",
          len(db.list_transactions(month="2026-06")) == 2)

    print("\n---- 预算 ----")
    check("默认预算", LedgerDatabase(db_path).budget() == DEFAULT_BUDGET)
    db.set_budget(2000)
    check("设置预算", db.budget() == 2000.0, str(db.budget()))
    db.set_budget(0)
    check("预算最小为 1", db.budget() == 1.0, str(db.budget()))
    db.set_budget(3500.5)
    check("预算支持小数", db.budget() == 3500.5, str(db.budget()))

    print("\n---- 设置与关闭 ----")
    db.set_setting("k", "v")
    check("settings 往返", db.get_setting("k") == "v")
    check("settings 默认值", db.get_setting("不存在", "d") == "d")
    db.close()
    check("close 后写操作报错", _raises_runtime(db))


def _raises_runtime(db: LedgerDatabase) -> bool:
    try:
        db.set_setting("x", "y")
        return False
    except RuntimeError:
        return True


def test_journals(db_path: str) -> None:
    db = LedgerDatabase(db_path)
    print("\n---- 日记 ----")
    check("初始无日记", db.get_journal("2026-03-01") is None)
    db.save_journal("2026-03-01", "<div>第一天<br></div>", "html")
    got = db.get_journal("2026-03-01")
    check("保存后读回", isinstance(got, Journal) and got.content == "<div>第一天<br></div>")
    db.save_journal("2026-03-01", "<div>改过了<br></div>", "html")
    check("同日期覆盖（upsert）",
          db.get_journal("2026-03-01").content == "<div>改过了<br></div>")
    check("覆盖不产生第二条", db.journal_count() == 1, str(db.journal_count()))
    expect_error("日记日期非法被拒",
                 lambda: db.save_journal("2026-3-1", "<div>x</div>"),
                 "日记日期无效")
    expect_error("日记格式非法被拒",
                 lambda: db.save_journal("2026-03-02", "x", "markdown"),
                 "日记格式无效")

    db.save_journal("2026-03-05", "<div>更早的日期但后写<br></div>", "html")
    db.save_journal("2026-02-01", "<div>二月<br></div>", "text")
    dates = [j.date for j in db.list_journals()]
    check("日记列表按日期降序", dates == ["2026-03-05", "2026-03-01", "2026-02-01"], str(dates))
    check("journal_dates 集合", db.journal_dates() == {"2026-03-01", "2026-03-05", "2026-02-01"})
    check("删除日记", db.delete_journal("2026-02-01") and db.journal_count() == 2)
    check("删除不存在的日记返回 False", db.delete_journal("1999-01-01") is False)
    db.close()


def test_checkins(db_path: str) -> None:
    db = LedgerDatabase(db_path)
    print("\n---- 签到 ----")
    tags = db.list_tags()
    check("默认 7 个标签", len(tags) == 7, str(len(tags)))
    check("标签按 sort_order 排序",
          [t.name for t in tags][:3] == ["工作", "学习", "运动"], str([t.name for t in tags]))
    check("默认标签颜色", db.tag_color("工作") == "#2563eb", db.tag_color("工作"))
    check("未签到日期颜色回退默认绿", db.tag_color("不存在") == "#176b4d")

    db.save_checkin("2026-04-01", "工作", "还行")
    got = db.get_checkin("2026-04-01")
    check("保存签到", isinstance(got, Checkin) and got.activity == "工作" and got.detail == "还行")
    db.save_checkin("2026-04-01", "运动", "跑步")
    check("签到 upsert 覆盖", db.get_checkin("2026-04-01").activity == "运动")

    db.save_checkin("2026-04-02", "", "只有心情")
    check("允许无标签但有心情", db.get_checkin("2026-04-02").detail == "只有心情")
    db.save_checkin("2026-04-03", "阅读", "")
    check("允许有标签但无心情", db.get_checkin("2026-04-03").activity == "阅读")

    expect_error("标签与心情都空被拒",
                 lambda: db.save_checkin("2026-04-04", "", "   "),
                 "请选择标签或填写今日心情")
    # 回归：不换行空格 U+00A0（网页粘贴常见）不能被当成“有心情”
    expect_error("只有不换行空格也被拒",
                 lambda: db.save_checkin("2026-04-04", "", "\u00a0\u00a0"),
                 "请选择标签或填写今日心情")
    expect_error("只有全角空格也被拒",
                 lambda: db.save_checkin("2026-04-04", "", "\u3000"),
                 "请选择标签或填写今日心情")
    check("normalize_space 处理各类空白",
          normalize_space("\u00a0\u3000  x  y \r\n") == "x y",
          repr(normalize_space("\u00a0\u3000  x  y \r\n")))
    expect_error("不存在的标签被拒",
                 lambda: db.save_checkin("2026-04-04", "瞎写的", "x"),
                 "签到标签无效")
    expect_error("心情超长被拒",
                 lambda: db.save_checkin("2026-04-04", "工作", "字" * 201),
                 "今日心情最多 200 个字")
    expect_error("签到日期非法被拒",
                 lambda: db.save_checkin("2026-4-1", "工作"),
                 "签到日期无效")

    month = db.checkins_in_month("2026-04")
    check("按月取签到", set(month) == {"2026-04-01", "2026-04-02", "2026-04-03"}, str(set(month)))
    check("按月计数", db.checkin_count_in_month("2026-04") == 3, str(db.checkin_count_in_month("2026-04")))
    check("空月计数为 0", db.checkin_count_in_month("2019-09") == 0)
    check("删除签到", db.delete_checkin("2026-04-03") and db.checkin_count_in_month("2026-04") == 2)

    print("\n---- 签到标签 ----")
    new_tag = db.add_tag("冥想")
    check("新增自定义标签", new_tag.name == "冥想" and new_tag.color == "#0891b2", str(new_tag))
    check("自定义标签排末尾", [t.name for t in db.list_tags()][-1] == "冥想")
    check("第二个自定义标签取第二色", db.add_tag("早睡").color == "#4f46e5")
    check("自定义标签数量 = 9", len(db.list_tags()) == 9)
    expect_error("重名标签被拒", lambda: db.add_tag("冥想"), "标签已存在")
    expect_error("空标签名被拒", lambda: db.add_tag("   "), "标签名称必填且最多 10 个字")
    expect_error("标签名超长被拒", lambda: db.add_tag("字" * 11), "标签名称必填且最多 10 个字")
    check("默认标签不可删", db.delete_tag("工作") is False)
    check("自定义标签可删", db.delete_tag("早睡") is True and len(db.list_tags()) == 8)
    db.save_checkin("2026-04-05", "冥想", "试了试")
    db.delete_tag("冥想")
    check("删标签后引用它的签到被置空",
          db.get_checkin("2026-04-05").activity == ""
          and db.get_checkin("2026-04-05").detail == "试了试")
    db.close()


def test_media(db_path: str) -> None:
    db = LedgerDatabase(db_path)
    print("\n---- 媒体文件 ----")
    url = db.save_journal_image(b"\x89PNG\r\n\x1a\n" + b"0" * 32, ".png")
    check("图片 URL 格式", url.startswith("/journal-images/") and url.endswith(".png"), url)
    name = url.rsplit("/", 1)[1]
    check("图片文件名是 32 位 hex", len(name) == 36, name)
    check("图片确实落盘",
          os.path.isfile(os.path.join(config.journal_images_dir(), name)))
    check("扩展名自动补点",
          db.save_journal_image(b"x", "jpg").endswith(".jpg"))

    vurl = db.save_journal_video(b"\x00\x00\x00\x20ftypisom" + b"0" * 32, ".mp4")
    check("视频 URL 格式", vurl.startswith("/journal-videos/"), vurl)
    check("视频确实落盘",
          os.path.isfile(os.path.join(config.journal_videos_dir(), vurl.rsplit("/", 1)[1])))
    check("无 .part 残留",
          not any(n.endswith(".part") for n in os.listdir(config.journal_videos_dir())))

    print("\n---- 引用解析（防误删核心）----")
    img_name = url.rsplit("/", 1)[1]
    vid_name = vurl.rsplit("/", 1)[1]
    db.save_journal("2026-07-01", f'<div><img src="/journal-images/{img_name}" alt="我的照片">'
                                  f'</div>', "html")
    refs = db.referenced_media()
    check("精确正则只取文件名", refs == {img_name}, str(refs))
    check("不会把 alt 属性吃进文件名", all(n.endswith(".png") for n in refs), str(refs))

    # 带引号/属性/后续标签的多种写法都要能解析
    db.save_journal("2026-07-02",
                    f"<div><img src='/journal-images/{img_name}'></div>"
                    f"<div><video src=\"/journal-videos/{vid_name}\" controls></video></div>",
                    "html")
    refs2 = db.referenced_media()
    check("单引号与双引号都能解析", refs2 == {img_name, vid_name}, str(refs2))
    check("宽松提取与精确提取一致",
          db.all_referenced_basenames() >= {img_name, vid_name})

    print("\n---- 孤立文件清理 ----")
    orphan_url = db.save_journal_image(b"\xff\xd8\xff" + b"0" * 32, ".jpg")
    orphan = orphan_url.rsplit("/", 1)[1]
    check("孤立图片已落盘",
          os.path.isfile(os.path.join(config.journal_images_dir(), orphan)))
    preview = db.cleanup_orphan_media(dry_run=True)
    check("dry_run 只报告不删", orphan in preview
          and os.path.isfile(os.path.join(config.journal_images_dir(), orphan)), str(preview))
    removed = db.cleanup_orphan_media()
    check("真正清理掉孤立文件", orphan in removed, str(removed))
    check("孤立文件已消失",
          not os.path.isfile(os.path.join(config.journal_images_dir(), orphan)))

    print("\n---- 被引用的文件绝不能被删（回归）----")
    db.cleanup_orphan_media()
    check("被引用的图片保留",
          os.path.isfile(os.path.join(config.journal_images_dir(), img_name)))
    check("被引用的视频保留",
          os.path.isfile(os.path.join(config.journal_videos_dir(), vid_name)))

    # 非媒体命名的文件不该被清理功能碰
    stray = os.path.join(config.journal_images_dir(), "readme.txt")
    with open(stray, "w", encoding="utf-8") as fh:
        fh.write("别删我")
    db.cleanup_orphan_media()
    check("不符合命名规则的文件不动", os.path.isfile(stray))
    os.remove(stray)
    db.close()


def test_migration(db_path: str) -> None:
    """迁移测试。db_path 由 set_data_dir() 指定，本组只验证迁移结果。"""
    print("\n==== 旧数据迁移 ====")
    build_legacy_source()

    check("迁移前未标记导入", already_imported() is False)
    report = import_money_data()
    check("迁移未跳过", report.skipped is False, report.reason)
    check("数据库已拷贝", report.db_copied is True)
    check("统计到 6 笔账（含 WAL 里那条）", report.transactions == 6, str(report.transactions))
    check("统计到 3 篇日记", report.journals == 3, str(report.journals))
    check("统计到 2 条签到", report.checkins == 2, str(report.checkins))
    check("统计到 7 个标签", report.tags == 7, str(report.tags))
    check("拷贝 2 张图片（1 张被引用 + 1 张孤立）", report.images_copied == 2,
          str(report.images_copied))
    check("拷贝 1 个视频", report.videos_copied == 1, str(report.videos_copied))
    check("无失败媒体", not report.media_failed, str(report.media_failed))

    # 关键：WAL 里的那条记录必须带过来（证明用了 backup() 而非拷文件）
    db = LedgerDatabase(config.ledger_db_path())
    found = [t for t in db.list_transactions() if t.purpose == "WAL 里的记录"]
    check("WAL 未落盘的记录也被迁移（用 backup API）", len(found) == 1, str(len(found)))
    check("预算设置迁移过来", db.budget() == 3500.0, str(db.budget()))
    db.close()

    check("迁移后标记为已导入", already_imported() is True)

    print("\n---- 幂等：重复导入不得翻倍 ----")
    second = import_money_data()
    check("第二次被跳过", second.skipped is True and second.reason == "旧数据已导入过",
          f"skipped={second.skipped} reason={second.reason}")
    db = LedgerDatabase(config.ledger_db_path())
    check("账目没有翻倍", db.transaction_count() == 6, str(db.transaction_count()))
    check("日记没有翻倍", db.journal_count() == 3, str(db.journal_count()))
    db.close()
    media_count = (len(os.listdir(config.journal_images_dir()))
                   + len(os.listdir(config.journal_videos_dir())))
    check("媒体没有翻倍", media_count == 3, str(media_count))

    # 强制重跑：媒体按同名去重，仍是 3
    third = import_money_data(force=True)
    check("force 重跑不跳过", third.skipped is False)
    check("force 重跑媒体全部跳过", third.media_skipped == 3, str(third.media_skipped))
    media_count2 = (len(os.listdir(config.journal_images_dir()))
                    + len(os.listdir(config.journal_videos_dir())))
    check("force 后媒体仍是 3 个", media_count2 == 3, str(media_count2))

    print("\n---- 源目录只读（绝不能被改动）----")
    source_db = os.path.join(os.environ["PLANSTICKY_MONEY_DIR"], "data", "ledger.db")
    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    src_count = src.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    src_journals = src.execute("SELECT COUNT(*) FROM journals").fetchone()[0]
    src.close()
    check("源库账目数不变", src_count == 6, str(src_count))
    check("源库日记数不变", src_journals == 3, str(src_journals))
    check("源媒体文件仍在",
          os.path.isfile(os.path.join(os.environ["PLANSTICKY_MONEY_DIR"],
                                      "data", "journal-images", IMG_A)))

    print("\n---- 源目录不存在时的行为 ----")
    missing = import_money_data(source_dir=os.path.join(_TMP, "根本没有这个目录"), force=True)
    check("源不存在时安全返回", missing.skipped is True
          and "找不到旧数据库" in missing.reason, missing.reason)


# ============================================== 真实旧库的只读验证（可选）
def test_real_source(db_path: str = "") -> None:
    """如果开发机上存在真实旧库，做一次只读校验（不导入、不修改）。"""
    real = r"F:\work\money\data\ledger.db"
    if not os.path.isfile(real):
        print("\n[SKIP] 真实旧库不存在，跳过只读验证")
        return
    print("\n==== 真实旧库只读验证 ====")
    before = os.stat(real).st_mtime_ns
    src = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    counts = {t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("transactions", "journals", "daily_checkins", "checkin_tags")}
    rows = src.execute("SELECT date, amount, type, category, purpose, note FROM transactions").fetchall()
    src.close()
    check("真实库可读", counts["transactions"] > 0, str(counts))
    check("真实库账目 >100 笔", counts["transactions"] > 100, str(counts["transactions"]))
    check("账目字段可解析", all(len(r) == 6 and float(r[1]) > 0 for r in rows))
    check("只读打开未改动源库", os.stat(real).st_mtime_ns == before)
    print(f"   真实库：{counts['transactions']} 笔账 / {counts['journals']} 篇日记 / "
          f"{counts['daily_checkins']} 条签到 / {counts['checkin_tags']} 个标签")


def set_data_dir(sub: str) -> str:
    """把数据目录切到 _TMP/<sub> 并返回该组专属的 ledger.db 路径。

    每组测试必须用独立目录：媒体文件是全局的（journal-images/），
    共用目录会让上一组留下的文件污染下一组的计数。
    """
    path = os.path.join(_TMP, sub)
    os.makedirs(path, exist_ok=True)
    os.environ["PLANSTICKY_DATA_DIR"] = path
    return os.path.join(path, "ledger.db")


def main() -> int:
    print("=" * 60)
    print("账本数据层 / 迁移 测试")
    print("=" * 60)

    test_validation(set_data_dir("t_validation"))
    test_transactions(set_data_dir("t_transactions"))
    test_journals(set_data_dir("t_journals"))
    test_checkins(set_data_dir("t_checkins"))
    test_media(set_data_dir("t_media"))
    test_migration(set_data_dir("t_migration"))
    test_real_source(set_data_dir("t_real"))

    print("\n" + "-" * 60)
    if FAILURES:
        print(f"LEDGER TEST FAILED: {len(FAILURES)} 项失败")
        for name in FAILURES:
            print("  x", name)
        return 1
    print("LEDGER TEST PASSED: 全部通过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:                        # noqa: BLE001
        traceback.print_exc()
        code = 2
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
