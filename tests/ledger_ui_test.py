"""账本 UI 集成测试：记账页 / 图表 / CSV / 日记页 / 签到页（离屏）。

运行：  .venv\\Scripts\\python.exe tests\\ledger_ui_test.py

与 `ledger_test.py` 的分工：那边测数据层与迁移，这里测**界面行为**——
页面构建、筛选联动、图表绘制不崩、CSV 往返、日记 HTML 存取、签到月历交互。

全程用临时目录与临时数据库，**不触碰真实数据**。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
_TMP = tempfile.mkdtemp(prefix="plansticky_ui_")
os.environ["PLANSTICKY_DATA_DIR"] = os.path.join(_TMP, "appdata")
os.environ["PLANSTICKY_LEDGER_DB"] = os.path.join(_TMP, "ledger.db")
os.environ["PLANSTICKY_SINGLE"] = "0"
# 未保存内容切换日期时的确认框在离屏环境下会永久阻塞，测试里自动选择“丢弃”
os.environ["PLANSTICKY_UNSAVED_ANSWER"] = "discard"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import csv                                                            # noqa: E402

from PySide6.QtCore import QDate, QPoint, Qt                          # noqa: E402
from PySide6.QtGui import QImage, QPainter                            # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

app = QApplication([])

from plansticky import config, journal_html                           # noqa: E402
from plansticky.checkin_page import CheckinPage, MonthCalendar        # noqa: E402
from plansticky.database import Database                              # noqa: E402
from plansticky.journal_page import JournalPage, today_key            # noqa: E402
from plansticky.ledger_charts import DonutChart, TrendChart           # noqa: E402
from plansticky.ledger_db import LedgerDatabase                       # noqa: E402
from plansticky.ledger_page import (LedgerPage, MonthPicker,          # noqa: E402
                                    TransactionDialog, parse_csv)
from plansticky.main_window import MainWindow, TAB_KEYS               # noqa: E402
from plansticky.theme import ThemeManager                             # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def render_to_image(widget, width: int = 420, height: int = 200) -> bool:
    """把控件画到离屏 QImage 上——能抓出 paintEvent 里的崩溃/异常。"""
    widget.resize(width, height)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0)
    try:
        # 注意：QWidget.render 会自己创建 painter，不能再传一个 QPainter 进去
        widget.render(image)
    except Exception:                 # noqa: BLE001
        traceback.print_exc()
        return False
    return not image.isNull()


# ============================================================== 记账页
def test_month_picker() -> None:
    print("\n---- 月份选择器 ----")
    picker = MonthPicker()
    this_month = QDate.currentDate().toString("yyyy-MM")
    check("默认本月", picker.month() == this_month, picker.month())
    check("未来月不可前进", not picker._btn_next.isEnabled())
    picker.shift(-1)
    prev = QDate.currentDate().addMonths(-1).toString("yyyy-MM")
    check("上一月", picker.month() == prev, picker.month())
    check("回到过去后可以前进", picker._btn_next.isEnabled())
    picker.shift(1)
    check("下一月回来", picker.month() == this_month, picker.month())
    # 跨年
    picker.set_month("2026-01")
    picker.shift(-1)
    check("跨年回溯", picker.month() == "2025-12", picker.month())
    picker.set_month("2026-12")
    picker.shift(1)
    check("跨年前进", picker.month() == "2027-01", picker.month())
    # 非法输入忽略
    before = picker.month()
    picker.set_month("乱写")
    check("非法月份被忽略", picker.month() == before, picker.month())
    picker.set_month("2026-13")
    check("13 月被忽略", picker.month() == before, picker.month())


def test_ledger_page(db: LedgerDatabase) -> None:
    print("\n---- 记账页 ----")
    page = LedgerPage(None)
    page._db = db          # 换成测试库
    page.refresh()
    app.processEvents()

    check("三个二级视图", page._stack.count() == 3, str(page._stack.count()))

    # 空态
    check("无数据时显示空态", page._empty.isVisible() or not page._model.rows,
          f"empty={page._empty.isVisible()} rows={len(page._model.rows)}")

    # 概览
    check("概览-本月支出标签", page._total_label.text().startswith("¥"),
          page._total_label.text())
    check("概览-笔数只有支出", "笔" in page._count_label.text(),
          page._count_label.text())

    # 表格筛选联动
    month = "2026-05"
    page.set_month(month)
    app.processEvents()
    check("切月后只显示该月", all(t.date.startswith(month) for t in page._model.rows),
          str([t.date for t in page._model.rows]))
    check("结果计数与行数一致",
          page._result_label.text() == f"{len(page._model.rows)} 笔",
          f"{page._result_label.text()} vs {len(page._model.rows)}")

    # 关键词筛选
    page._search.setText("地铁")
    app.processEvents()
    check("关键词筛选生效",
          all("地铁" in (t.purpose + t.note) for t in page._model.rows),
          str([t.purpose for t in page._model.rows]))
    page._search.setText("")
    app.processEvents()

    # 类型筛选
    page._filter_type.setCurrentIndex(2)      # 收入
    app.processEvents()
    check("类型筛选=收入", all(t.is_income for t in page._model.rows),
          str([t.type for t in page._model.rows]))
    page._filter_type.setCurrentIndex(0)
    app.processEvents()

    # 分类筛选
    page._filter_cat.setCurrentIndex(1)       # 餐饮
    app.processEvents()
    check("分类筛选=餐饮", all(t.category == "餐饮" for t in page._model.rows),
          str([t.category for t in page._model.rows]))
    page._filter_cat.setCurrentIndex(0)
    app.processEvents()

    # 日期范围开关
    page._range_on.setChecked(True)
    app.processEvents()
    check("勾选范围后月份选择器禁用", not page._picker.isEnabled())
    page._date_from.setDate(QDate(2026, 5, 2))
    page._date_to.setDate(QDate(2026, 5, 3))
    app.processEvents()
    in_range = [t for t in page._model.rows if "2026-05-02" <= t.date <= "2026-05-03"]
    check("日期范围筛选生效", len(page._model.rows) == len(in_range),
          f"{[t.date for t in page._model.rows]}")
    page._range_on.setChecked(False)
    app.processEvents()
    check("取消范围后月份恢复", page._picker.isEnabled())

    # 图表存在且能绘制
    check("趋势图存在", isinstance(page._trend, TrendChart))
    check("环形图存在", isinstance(page._donut, DonutChart))
    page._stack.setCurrentIndex(1)
    app.processEvents()
    check("趋势图可绘制不崩", render_to_image(page._trend, 360, 170))
    check("环形图可绘制不崩", render_to_image(page._donut, 130, 130))

    # 趋势档位
    for idx, days in enumerate((7, 30, 90)):
        page._trend_seg.set_current_index(idx)
        app.processEvents()
        check(f"趋势档位 {days} 天", page._trend.days == days, str(page._trend.days))
        check(f"趋势标题 {days} 天", f"{days} 天" in page._trend_title.text(),
              page._trend_title.text())

    # 预算编辑
    page._budget_edit.setValue(1234)
    page._on_budget_changed()
    app.processEvents()
    check("预算写入库", db.budget() == 1234.0, str(db.budget()))
    summary = db.month_summary(month)
    expect_rate = int(round(summary.expense / 1234 * 100)) if summary.expense else 0
    check("预算百分比按新预算重算",
          page._budget_rate.text() == f"{expect_rate}%",
          f"{page._budget_rate.text()} vs {expect_rate}%")
    check("预算提示含新预算",
          "1,234.00" in page._budget_bar.toolTip(), page._budget_bar.toolTip())

    # 收支切换按钮样式
    dialog = TransactionDialog(db, None)
    dialog._set_type("income")
    check("切收入选中收入按钮",
          dialog._btn_income.isChecked() and not dialog._btn_expense.isChecked())
    dialog._set_type("expense")
    check("切支出选中支出按钮",
          dialog._btn_expense.isChecked() and not dialog._btn_income.isChecked())
    check("分类宫格 8 项", len(dialog._categories._buttons) == 8,
          str(len(dialog._categories._buttons)))
    dialog._categories.set_selected("娱乐")
    check("分类可选", dialog._categories.selected() == "娱乐",
          dialog._categories.selected())
    dialog.close()
    page.deleteLater()


def test_add_edit_delete_flow(db: LedgerDatabase) -> None:
    print("\n---- 增删改流程（走数据层，模拟页面动作）----")
    page = LedgerPage(None)
    page._db = db
    page.set_month("2026-06")
    app.processEvents()

    before = len(page._model.rows)
    new_id = db.add_transaction("2026-06-10", 88.0, "expense", "娱乐", "电影票", "周末")
    page.refresh()
    app.processEvents()
    check("新增后行数 +1", len(page._model.rows) == before + 1,
          f"{before} -> {len(page._model.rows)}")
    check("新增行出现在表格首行", page._model.rows[0].id == new_id,
          str(page._model.rows[0]))

    db.update_transaction(new_id, "2026-06-11", 99.0, "expense", "娱乐", "电影票改", "n")
    page.refresh()
    app.processEvents()
    got = [t for t in page._model.rows if t.id == new_id][0]
    check("编辑后金额与日期已更新", got.amount == 99.0 and got.date == "2026-06-11",
          f"{got.amount} {got.date}")

    db.delete_transaction(new_id)
    page.refresh()
    app.processEvents()
    check("删除后从表格消失",
          all(t.id != new_id for t in page._model.rows))
    page.deleteLater()


# ============================================================== CSV
def test_csv_roundtrip(db: LedgerDatabase) -> None:
    print("\n---- CSV 往返（修类型丢失缺陷）----")
    page = LedgerPage(None)
    page._db = db
    page.refresh()

    path = os.path.join(_TMP, "roundtrip.csv")
    rows = db.list_transactions()
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["日期", "金额", "类型", "分类", "用途", "备注"])
        for tx in rows:
            writer.writerow([tx.date, f"{tx.amount:.2f}",
                             "收入" if tx.is_income else "支出",
                             tx.category, tx.purpose, tx.note])

    items = parse_csv(path)
    check("解析出全部行", len(items) == len(rows), f"{len(items)} vs {len(rows)}")
    income_in = [i for i in items if i["type"] == "income"]
    income_db = [t for t in rows if t.is_income]
    check("收入类型没有丢", len(income_in) == len(income_db),
          f"{len(income_in)} vs {len(income_db)}")

    # 关键回归：导入后收入仍是收入
    target = os.path.join(_TMP, "import_target.db")
    fresh = LedgerDatabase(target)
    inserted, skipped = fresh.import_transactions(items)
    check("导入条数一致", inserted == len(rows) and skipped == 0,
          f"inserted={inserted} skipped={skipped}")
    fresh_income = [t for t in fresh.list_transactions() if t.is_income]
    check("导入后收入仍是收入（旧版会变成支出）",
          len(fresh_income) == len(income_db),
          f"{len(fresh_income)} vs {len(income_db)}")
    # 兼容旧版导出的英文类型
    legacy = [{"date": "2026-07-01", "amount": "10", "type": "income",
               "category": "其他", "purpose": "旧格式收入", "note": ""}]
    check("英文 income 也被识别为收入",
          fresh.import_transactions(legacy)[0] == 1
          and any(t.is_income for t in fresh.list_transactions()
                  if t.purpose == "旧格式收入"))
    fresh.close()

    # 旧版 5 列格式
    old_path = os.path.join(_TMP, "old5.csv")
    with open(old_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["日期", "金额", "分类", "用途", "备注"])
        writer.writerow(["2026-08-01", "15.5", "交通", "公交", ""])
    old_items = parse_csv(old_path)
    check("5 列旧格式可解析", len(old_items) == 1 and old_items[0]["type"] == "expense"
          and old_items[0]["category"] == "交通", str(old_items))
    check("表头行被跳过", old_items[0]["date"] == "2026-08-01")

    # 空文件 / 只有表头
    empty_path = os.path.join(_TMP, "empty.csv")
    with open(empty_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["日期", "金额", "类型", "分类", "用途", "备注"])
    check("只有表头返回空列表", parse_csv(empty_path) == [])

    # BOM 处理
    check("BOM 不影响首行解析", parse_csv(path)[0]["date"] == rows[0].date,
          parse_csv(path)[0]["date"])
    page.deleteLater()


# ============================================================== 日记页
def test_journal_page() -> None:
    print("\n---- 日记页 ----")
    page = JournalPage()
    page.show()
    app.processEvents()
    check("默认打开今天", page._day == today_key(), page._day)
    check("初始无未保存标记", page.has_unsaved() is False)

    # 写入并保存
    page._editor.setHtml("<div>今天写了日记</div>")
    app.processEvents()
    check("编辑后标记未保存", page.has_unsaved() is True)
    check("状态显示未保存", "未保存" in page._status.text(), page._status.text())
    check("保存成功", page.save(quiet=True) is True)
    check("保存后清除未保存标记", page.has_unsaved() is False)

    stored = page._db.get_journal(today_key())
    check("落库为旧版 HTML 格式", stored is not None
          and stored.content == "<div>今天写了日记</div>",
          stored.content if stored else "None")
    check("落库格式标记为 html", stored.format == "html", stored.format)

    # 归档列表
    check("归档显示 1 篇", page._count_label.text() == "1 篇", page._count_label.text())
    check("归档有 1 行", page._archive.count() == 1, str(page._archive.count()))

    # 重新载入
    page._load()
    app.processEvents()
    check("重新载入内容一致",
          journal_html.plain_text(journal_html.doc_to_html(page._editor.document()))
          == "今天写了日记")

    # 图表式富文本往返
    page._editor.setHtml("<div>这是<b>粗体</b>和<i>斜体</i></div>")
    page.save(quiet=True)
    stored2 = page._db.get_journal(today_key())
    check("富文本格式保留",
          "<b>粗体</b>" in stored2.content and "<i>斜体</i>" in stored2.content,
          stored2.content)

    # 空内容拒绝保存
    page._editor.clear()
    app.processEvents()
    check("空内容保存被拒", page.save(quiet=True) is False)
    # 回归：只有不换行空格（网页粘贴常见）也必须算空
    page._editor.setHtml("<div>\u00a0\u00a0\u00a0</div>")
    app.processEvents()
    check("只有不换行空格也算空内容", page.save(quiet=True) is False)
    check("plain_text 处理 U+00A0",
          journal_html.plain_text("<div>\u00a0\u00a0</div>") == "")
    check("is_blank 识别 U+00A0", journal_html.is_blank("<div>\u00a0</div>") is True)
    check("is_blank 认图片为有内容",
          journal_html.is_blank('<div><img src="/journal-images/' + "a" * 32
                                + '.jpg"></div>') is False)

    # 切换日期（未保存时 confirm_discard 会弹窗，这里测 force 路径）
    other = QDate.currentDate().addDays(-3).toString("yyyy-MM-dd")
    page._editor.setHtml("<div>临时的</div>")
    app.processEvents()
    check("切日期成功", page.set_date(other) is True)
    check("切日期后日期已变", page._day == other, page._day)
    check("切日期后清空未保存标记", page.has_unsaved() is False)
    check("新日期无内容", page._db.get_journal(other) is None)

    # 图片插入 + 保存（用真实 PNG 字节）
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 8 +
           b"\x08\x06\x00\x00\x00" + b"\x00" * 4 + b"\x00" * 20)
    image_path = os.path.join(_TMP, "shot.png")
    with open(image_path, "wb") as fh:
        fh.write(png)
    # 直接压一个合法的小 PNG（QImage 生成），确保能被解码
    from PySide6.QtGui import QImage as _QImage
    real = _QImage(24, 16, _QImage.Format.Format_RGB32)
    real.fill(0xFF3366)
    check("测试图片已保存", real.save(image_path, "PNG"))

    page.set_date(today_key())
    before_files = set(os.listdir(config.journal_images_dir()))
    check("插入图片成功", page._insert_image_file(image_path) is True)
    check("待保存文件用 .pending- 前缀（不保存不留垃圾）",
          any(n.startswith(".pending-") for n in os.listdir(config.journal_images_dir())),
          str(sorted(os.listdir(config.journal_images_dir()))[:4]))
    check("插入待保存图片后标记未保存", page.has_unsaved() is True)
    check("保存带图片的日记成功", page.save(quiet=True) is True)
    after_files = set(os.listdir(config.journal_images_dir()))
    check("保存后转为正式文件", not any(n.startswith(".pending-") for n in after_files),
          str(sorted(after_files - before_files)))
    check("新增了 1 个媒体文件", len(after_files - before_files) == 1,
          str(after_files - before_files))

    record = page._db.get_journal(today_key())
    refs = journal_html.referenced_media(record.content)
    check("日记引用了这张图", len(refs) == 1, str(refs))
    check("引用的文件确实存在",
          all(os.path.isfile(os.path.join(config.journal_images_dir(), n)) for n in refs),
          str(refs))

    # 未引用的待保存媒体会被丢弃（不产生孤立文件）
    orphan_before = set(os.listdir(config.journal_images_dir()))
    page.set_date(other)
    check("切换到无日记日期", page._day == other, page._day)
    # 插入图片但不保存，然后切走：文件应被清理
    page._insert_image_file(image_path)
    pending_now = [n for n in os.listdir(config.journal_images_dir())
                   if n.startswith(".pending-")]
    check("未保存时存在临时文件", len(pending_now) == 1, str(pending_now))
    page.set_date(today_key())
    leftovers = [n for n in os.listdir(config.journal_images_dir())
                 if n.startswith(".pending-")]
    check("切日期后临时文件被清理（旧版会留孤立文件）", not leftovers, str(leftovers))

    # 孤立媒体清理不应该删掉被引用的
    orphans = page._db.cleanup_orphan_media(dry_run=True)
    check("孤立扫描不误报被引用文件",
          all(n not in refs for n in orphans), f"orphans={orphans} refs={refs}")
    page.deleteLater()


# ============================================================== 签到页
def test_checkin_page() -> None:
    print("\n---- 签到页 ----")
    page = CheckinPage()
    page.show()
    app.processEvents()
    check("默认选中今天", page._selected == today_key(), page._selected)
    check("月历控件存在", isinstance(page._calendar, MonthCalendar))
    check("默认 7 个标签胶囊", len(page._chips) == 7, str(len(page._chips)))

    # 月历绘制
    check("月历可绘制不崩", render_to_image(page._calendar, 320, 200))
    cells = page._calendar._cells
    check("月历格子数 = 当月天数",
          len(cells) == QDate.currentDate().daysInMonth(),
          f"{len(cells)} vs {QDate.currentDate().daysInMonth()}")

    # 点击某天
    target = QDate.currentDate().toString("yyyy-MM-dd")
    page._calendar.dateClicked.emit(target)
    app.processEvents()
    check("点击日期切换选中", page._selected == target, page._selected)
    check("日期标签显示月日", page._date_label.text().startswith(
        f"{QDate.currentDate().month()} 月"), page._date_label.text())
    check("未签到时状态正确", "未签到" in page._status_label.text(),
          page._status_label.text())

    # 选标签 + 写心情 + 保存
    page._toggle_tag("工作")
    check("标签被选中", page._activity == "工作", page._activity)
    page._toggle_tag("工作")
    check("再点同一标签取消选中", page._activity == "", page._activity)
    page._toggle_tag("运动")
    page._mood.setPlainText("今天跑步了")
    check("保存成功（无异常）", _save_ok(page))
    record = page._db.get_checkin(target)
    check("签到已落库", record is not None and record.activity == "运动"
          and record.detail == "今天跑步了",
          str(record))
    check("状态变为已签到", "已签到" in page._status_label.text(),
          page._status_label.text())
    check("删除按钮出现", page._btn_delete.isVisible())
    check("月历标记了该日", target in page._calendar._marks,
          str(list(page._calendar._marks)[:3]))

    # 只有心情、没有标签也可以保存
    page._toggle_tag("运动")
    page._mood.setPlainText("只有心情")
    check("无标签有心情可保存", _save_ok(page))
    check("落库 activity 为空", page._db.get_checkin(target).activity == "",
          page._db.get_checkin(target).activity)

    # 两者都空则拒绝
    page._mood.setPlainText("   ")
    check("标签与心情都空时拒绝保存", _save_ok(page) is False)

    # 新增自定义标签
    added = page._db.add_tag("冥想")
    page.refresh()
    app.processEvents()
    check("自定义标签出现在界面上", "冥想" in page._chips, str(list(page._chips)))
    check("标签总数 8", len(page._chips) == 8, str(len(page._chips)))

    # 翻月
    this_month = (QDate.currentDate().year(), QDate.currentDate().month())
    page.shift_month(-1)
    app.processEvents()
    prev = QDate.currentDate().addMonths(-1)
    check("上月翻页", (page._year, page._month) == (prev.year(), prev.month()),
          f"{page._year}-{page._month}")
    check("翻到过去月后选中该月 1 日", page._selected.endswith("-01"), page._selected)
    check("过去月月历可绘制", render_to_image(page._calendar, 320, 200))
    page.goto_today()
    app.processEvents()
    check("回到今天", (page._year, page._month) == this_month
          and page._selected == today_key(), f"{page._year}-{page._month} {page._selected}")

    # 未来日期可签到（旧版行为）
    future = QDate.currentDate().addDays(5).toString("yyyy-MM-dd")
    page.set_date(future)
    app.processEvents()
    check("未来日期可选中", page._selected == future, page._selected)
    page._toggle_tag("学习")
    check("未来日期可签到", _save_ok(page) is True)
    check("未来日期签到已落库", page._db.get_checkin(future) is not None)

    page.deleteLater()


def _save_ok(page: CheckinPage) -> bool:
    """调用保存并返回页面自报的结果。

    注意：不能靠“数据库里有没有这条记录”判断成功与否——签到是 upsert，
    校验失败时旧记录仍在，会被误判成成功。
    """
    result = page.save()
    app.processEvents()
    return bool(result)


# ============================================================== 整体集成
def test_main_window_integration() -> None:
    print("\n---- 主窗口集成（5 Tab）----")
    db = Database(config.db_path())
    theme = ThemeManager(db)
    win = MainWindow(db, theme)
    win.show()
    app.processEvents()

    check("5 个 Tab", len(TAB_KEYS) == 5 and win._seg._buttons.__len__() == 5)
    for idx, key in enumerate(TAB_KEYS):
        win._seg.set_current_index(idx)
        app.processEvents()
        check(f"Tab {key} 可切换且页面对应",
              win._stack.currentIndex() == idx
              and win._stack.widget(idx) is not None,
              f"idx={win._stack.currentIndex()}")

    # 每个页面都能绘制（抓 paintEvent 崩溃）
    for idx, key in enumerate(TAB_KEYS):
        win._seg.set_current_index(idx)
        app.processEvents()
        check(f"页面 {key} 可渲染", render_to_image(win, 420, 560))

    # 提示条在真实窗口里可见
    win.toast("测试消息")
    app.processEvents()
    check("提示条可见", win._toast.isVisible() and win._toast.text() == "测试消息",
          f"visible={win._toast.isVisible()}")
    win.toast("出错了", "error")
    app.processEvents()
    check("错误提示级别正确", win._toast.property("level") == "error")

    # 主题切换后各页面仍可渲染
    theme.set_key("dark")
    app.processEvents()
    for idx, key in enumerate(TAB_KEYS):
        win._seg.set_current_index(idx)
        app.processEvents()
        check(f"深色下 {key} 可渲染", render_to_image(win, 420, 560))
    theme.set_key("light")
    app.processEvents()

    # 托盘跳转接口
    win.show_journal()
    app.processEvents()
    check("show_journal 切到日记页", win._stack.currentIndex() == 3,
          str(win._stack.currentIndex()))
    win.show_checkin()
    app.processEvents()
    check("show_checkin 切到签到页", win._stack.currentIndex() == 4,
          str(win._stack.currentIndex()))
    win.show_ledger("2026-05")
    app.processEvents()
    check("show_ledger 切到记账页并设月份",
          win._stack.currentIndex() == 2
          and win._ledger_page._picker.month() == "2026-05",
          f"idx={win._stack.currentIndex()} month={win._ledger_page._picker.month()}")

    db.close()


def test_media_cleanup_page() -> None:
    print("\n---- 记账页的媒体清理入口 ----")
    page = LedgerPage(None)
    fresh = LedgerDatabase(os.path.join(_TMP, "cleanup.db"))
    page._db = fresh
    # 造一个孤立文件和一个被引用文件
    ref_url = fresh.save_journal_image(b"\x89PNG\r\n\x1a\n" + b"0" * 20, ".png")
    orphan_url = fresh.save_journal_image(b"\xff\xd8\xff" + b"0" * 20, ".jpg")
    ref_name = ref_url.rsplit("/", 1)[1]
    orphan_name = orphan_url.rsplit("/", 1)[1]
    fresh.save_journal("2026-01-01", f'<div><img src="{ref_url}"></div>', "html")
    preview = fresh.cleanup_orphan_media(dry_run=True)
    check("扫描发现孤立文件", orphan_name in preview and ref_name not in preview,
          str(preview))
    removed = fresh.cleanup_orphan_media(dry_run=False)
    # 注意：只能断言「我造的那个孤立文件被删」「我引用的那个被保留」。
    # 目录里还有前面日记测试留下的文件，它们同样是孤立文件，也会被清掉。
    check("我的孤立文件被删除", orphan_name in removed, str(removed))
    check("同一轮的孤立文件全部清掉",
          all(not os.path.isfile(os.path.join(config.journal_images_dir(), n))
              for n in removed), str(removed))
    check("被引用文件仍在",
          os.path.isfile(os.path.join(config.journal_images_dir(), ref_name)))
    # 清理后再扫一次应该干净了
    check("再次扫描无孤立文件", fresh.cleanup_orphan_media(dry_run=True) == [])
    fresh.close()
    page.deleteLater()


def main() -> int:
    print("=" * 60)
    print("账本 UI 集成测试（记账 / 图表 / CSV / 日记 / 签到）")
    print("=" * 60)

    test_month_picker()

    ui_db = LedgerDatabase(os.path.join(_TMP, "ui_test.db"))
    ui_db.add_transaction("2026-05-01", 28.5, "expense", "餐饮", "午餐", "楼下")
    ui_db.add_transaction("2026-05-02", 12.0, "expense", "交通", "地铁", "")
    ui_db.add_transaction("2026-05-03", 8000.0, "income", "其他", "工资", "5月")
    ui_db.add_transaction("2026-06-01", 66.0, "expense", "娱乐", "电影", "imax")
    test_ledger_page(ui_db)
    test_add_edit_delete_flow(ui_db)
    test_csv_roundtrip(ui_db)
    ui_db.close()

    test_journal_page()
    test_checkin_page()
    test_media_cleanup_page()
    test_main_window_integration()

    print("\n" + "-" * 60)
    if FAILURES:
        print(f"UI TEST FAILED: {len(FAILURES)} 项失败")
        for name in FAILURES:
            print("  x", name)
        return 1
    print("UI TEST PASSED: 全部通过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:                        # noqa: BLE001
        traceback.print_exc()
        code = 2
    finally:
        app.quit()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
