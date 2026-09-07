"""离屏冒烟测试：不弹真实窗口，验证数据层 + UI 主流程可运行。

运行：  .venv\\Scripts\\python.exe tests\\smoke_test.py
退出码 0 = 全部通过；任何断言失败会打印 FAIL 并退出非 0。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

# ---- 环境：离屏渲染 + 隔离数据目录 + 关闭单实例 ----
os.environ["QT_QPA_PLATFORM"] = "offscreen"
_TMP = tempfile.mkdtemp(prefix="plansticky_smoke_")
os.environ["PLANSTICKY_DATA_DIR"] = _TMP
os.environ["PLANSTICKY_SINGLE"] = "0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication

from plansticky.database import Database
from plansticky.theme import ThemeManager

app = QApplication([])

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    db_path = os.path.join(_TMP, "plans.db")
    db = Database(db_path)

    # ============================================================ 数据层
    # settings 往返
    db.set_setting("hello", "world")
    check("settings roundtrip", db.get_setting("hello") == "world")
    db.set_setting_bool("flag", True)
    check("settings bool", db.get_setting_bool("flag") is True)

    # 长期任务增/查
    a = db.add_task("long", "学习英语")
    b = db.add_task("long", "每周健身三次")
    c = db.add_task("long", "完成个人网站")
    rows = db.list_tasks("long")
    check("long add order", [r["content"] for r in rows] ==
          ["学习英语", "每周健身三次", "完成个人网站"], str(rows))

    # 完成状态与计数
    db.set_done(a, True)
    done, total = db.counts("long")
    check("long counts", (done, total) == (1, 3), f"{done}/{total}")

    # 排序：把第一个挪到最后（pos 语义）
    db.reorder("long", None, [b, c, a])
    rows = db.list_tasks("long")
    check("long reorder", [r["content"] for r in rows] ==
          ["每周健身三次", "完成个人网站", "学习英语"])

    # 短期：两天数据互不影响（日期用远离“今天”的 2030 年，避免撞上运行日）
    d1 = QDate(2030, 1, 5).toString("yyyy-MM-dd")
    d2 = QDate(2030, 1, 6).toString("yyyy-MM-dd")
    x1 = db.add_task("day", "写周报", d1)
    x2 = db.add_task("day", "买牛奶", d1)
    y1 = db.add_task("day", "开例会", d2)
    check("day independence",
          len(db.list_tasks("day", d1)) == 2 and len(db.list_tasks("day", d2)) == 1)
    db.set_done(x1, True)
    check("day counts", db.counts("day", d1) == (1, 2), str(db.counts("day", d1)))
    check("day2 unaffected", db.counts("day", d2) == (0, 1))

    # 改内容 / 删除
    db.update_content(x2, "买牛奶和面包")
    check("update content", db.list_tasks("day", d1)[1]["content"] == "买牛奶和面包")
    db.delete_task(y1)
    check("delete", len(db.list_tasks("day", d2)) == 0)

    # ============================================================ 主题
    theme = ThemeManager(db)
    theme.set_key("dark")
    check("theme dark applied", "#212121" in (app.styleSheet() or ""), "QSS 无深色底色")
    theme.set_key("light")
    check("theme light applied", "#FFFFFF" in (app.styleSheet() or ""))

    # ============================================================ UI
    from plansticky.calendar_popup import CalendarPopup
    from plansticky.main_window import MainWindow

    win = MainWindow(db, theme)
    win.show()
    app.processEvents()
    check("default tab = day", win._stack.currentIndex() == 1)

    # Tab 切换回归：分段按钮互斥、重复点击已激活 Tab 页面不跳变
    def seg_states():
        return [b.isChecked() for b in win._seg._buttons]
    win._seg._buttons[0].click()      # 切长期
    app.processEvents()
    check("tab to long exclusive", win._stack.currentIndex() == 0
          and seg_states() == [True, False], str(seg_states()))
    win._seg._buttons[0].click()      # 重复点击长期：不应切走/全灭
    app.processEvents()
    check("tab re-click stable", win._stack.currentIndex() == 0
          and seg_states() == [True, False], str(seg_states()))
    win._seg._buttons[1].click()      # 切短期
    app.processEvents()
    check("tab to day exclusive", win._stack.currentIndex() == 1
          and seg_states() == [False, True], str(seg_states()))
    win._seg._buttons[1].click()
    app.processEvents()
    check("tab re-click day stable", win._stack.currentIndex() == 1
          and seg_states() == [False, True], str(seg_states()))

    # 今天加两条：第一条回车提交（Enter 路径）
    win.show_today()
    bar = win._day_add
    bar._open_editor()
    bar._edit.setText("写周报")
    bar._edit.returnPressed.emit()   # Enter 保存并停留
    bar._edit.setText("买牛奶")
    bar._edit.returnPressed.emit()
    app.processEvents()
    done, total = db.counts("day", QDate.currentDate().toString("yyyy-MM-dd"))
    check("ui add two tasks", (done, total) == (0, 2), f"{done}/{total}")
    check("count label today", win._count_label.text() == "今日 0/2",
          win._count_label.text())

    # 勾掉第一条 -> 分区移动 + 计数 1/2
    first_id = db.list_tasks("day", QDate.currentDate().toString("yyyy-MM-dd"))[0]["id"]
    db.set_done(first_id, True)
    win._day_view.reload()
    app.processEvents()
    check("partition pending=1 done=1",
          len(win._day_view._pending_rows) == 1 and len(win._day_view._done_rows) == 1)
    check("count label after done", win._count_label.text() == "今日 1/2",
          win._count_label.text())

    # 行内编辑保存
    row = win._day_view._pending_rows[0]
    row.start_edit()
    row._cell.editor.setText("写周报（改）")
    row._cell.editor.commitText.emit("写周报（改）")
    app.processEvents()
    cur = db.list_tasks("day", QDate.currentDate().toString("yyyy-MM-dd"))
    check("inline edit saved", any(t["content"] == "写周报（改）" for t in cur))

    # 前一天切换 / 空态 / 今天按钮
    win._shift_day(-1)
    app.processEvents()
    yesterday = QDate.currentDate().addDays(-1)
    check("date nav shows yesterday",
          win._selected_date == yesterday and win._day_view.day ==
          yesterday.toString("yyyy-MM-dd"))
    check("empty state hint visible", win._day_view._empty_hint is not None
          and win._day_view._empty_hint.isVisible())
    win.show_today()
    check("today button back", win._selected_date == QDate.currentDate())

    # 回归：空 -> 非空 -> 空 反复切换不崩溃
    # （旧版把空态标签做成常驻控件，非空时被 deleteLater 删除，
    #   再次切到空日期 addWidget 即抛 “C++ object already deleted”）
    win._shift_day(1)      # 昨天空 -> 今天非空 -> 明天空
    app.processEvents()
    check("empty re-show after non-empty", win._day_view._empty_hint is not None
          and win._day_view._empty_hint.isVisible())
    win.show_today()
    app.processEvents()
    check("back to today after empty cycle",
          win._selected_date == QDate.currentDate())

    # “今天”语义回归：今天视图下按钮显示「今天」且置灰；
    # 其它日期下显示「回到今天」且可点，占位符写明日期
    check("today btn text+disabled on today",
          win._btn_today.text() == "今天" and not win._btn_today.isEnabled())
    check("addbar hint 今日 on today", "今日" in win._day_add._edit.placeholderText())
    win._shift_day(1)      # 到明天
    app.processEvents()
    check("today btn shows 回到今天 on other day",
          win._btn_today.text() == "回到今天" and win._btn_today.isEnabled())
    hint = win._day_add._edit.placeholderText()
    check("addbar hint states date on other day",
          "今日" not in hint and "日计划" in hint, hint)
    win.show_today()
    app.processEvents()

    # 长期页：开关 + 添加 + 隐藏已完成（原库中已有 3 条：每周健身三次/完成个人网站/学习英语[done]）
    win._seg.set_current_index(0)   # 切到长期
    app.processEvents()
    lbar = win._long_add
    lbar._open_editor()
    lbar._edit.setText("学做饭")
    lbar._edit.returnPressed.emit()
    lbar._edit.setText("看书")
    lbar._edit.returnPressed.emit()
    app.processEvents()
    check("long page add two", len(db.list_tasks("long")) == 5,
          str(len(db.list_tasks("long"))))
    # 把第一条未完成（每周健身三次）勾掉 -> 完成区应有 2 条
    lt = db.list_tasks("long")
    db.set_done(lt[0]["id"], True)   # lt[0] 是未完成的 每周健身三次
    win._long_view.reload()
    app.processEvents()
    check("long done section visible",
          len(win._long_view._done_rows) == 2 and len(win._long_view._pending_rows) == 3,
          f"done={len(win._long_view._done_rows)} pending={len(win._long_view._pending_rows)}")
    win._hide_done_sw.setChecked(True)
    app.processEvents()
    check("hide done removes section", len(win._long_view._done_rows) == 0
          and len(win._long_view._pending_rows) == 3,
          f"pending={len(win._long_view._pending_rows)}")
    win._hide_done_sw.setChecked(False)
    app.processEvents()
    check("unhide restores", len(win._long_view._done_rows) == 2)

    # 拖拽重排（模拟落库路径）+ 视图重建
    ids = [r.task_id for r in win._long_view._pending_rows]
    rotated = ids[1:] + ids[:1]
    db.reorder("long", None, rotated)
    win._long_view.reload()
    app.processEvents()
    got = [r.task_id for r in win._long_view._pending_rows]
    check("drag-reorder effect", got == rotated, f"{got} != {rotated}")

    # 主题按钮不崩；日历弹层可构建
    win._theme.set_key("system")
    theme.set_key("light")
    popup = CalendarPopup(win, QDate.currentDate())
    check("calendar popup builds", popup._cal is not None)

    # 几何记忆
    win.resize(360, 560)
    win.move(120, 130)
    win._save_geometry_now()
    geo = db.get_geometry()
    check("geometry saved", geo == {"x": 120, "y": 130, "w": 360, "h": 560}, str(geo))

    # 置顶持久化 + 窗口保持可见 + flag 真正生效
    win.set_pinned(True)
    app.processEvents()
    check("pin persisted", db.get_setting_bool("window_pinned") is True)
    check("pin keeps window visible", win.isVisible())
    check("pin flag applied",
          bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
    win.set_pinned(False)
    app.processEvents()
    check("unpin keeps window visible", win.isVisible())
    check("unpin flag cleared",
          not bool(win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))

    # ---- 多日列表模式 ----
    from PySide6.QtCore import QDate as _QDate
    t_key = _QDate.currentDate().toString("yyyy-MM-dd")
    tm_key = _QDate.currentDate().addDays(1).toString("yyyy-MM-dd")
    # 明天放两条（今天的 2 条在更早的步骤里已添加且完成 1 条）
    db.add_task("day", "明天的计划A", tm_key)
    db.add_task("day", "明天的计划B", tm_key)
    win._seg.set_current_index(1)          # 切到短期 Tab
    win._day_mode_seg.set_current_index(0)  # 确保从单日进入
    app.processEvents()
    win._day_mode_seg.set_current_index(1)  # 切到多日列表
    app.processEvents()
    check("day mode switched to list", win._day_stack.currentIndex() == 1
          and db.get_setting("day_mode") == "list")
    headers = win._day_list._day_headers
    check("list has today & tomorrow headers",
          t_key in headers and tm_key in headers)
    t_txt = headers[t_key].text()
    tm_txt = headers[tm_key].text()
    check("today header marked 今天", t_txt.startswith("今天") and "1/2" in t_txt,
          t_txt)
    check("tomorrow header shows count", "0/2" in tm_txt, tm_txt)
    rows_tm = win._day_list._pending.get(tm_key, [])
    check("list rows under tomorrow", len(rows_tm) == 2
          and all(r._handle is None for r in rows_tm))
    # 列表内直接勾选完成 -> 计数刷新
    rows_tm[0]._check.click()
    app.processEvents()
    check("toggle done inside list mode",
          db.counts("day", tm_key) == (1, 2)
          and "1/2" in win._day_list._day_headers[tm_key].text())
    # 点击标题 -> 回到单日并打开该天
    win._day_list.dateActivated.emit(t_key)
    app.processEvents()
    check("list date click opens single day",
          win._day_stack.currentIndex() == 0
          and win._day_mode == "single"
          and win._selected_date.toString("yyyy-MM-dd") == t_key)

    # ---- 完成图（单月月历）----
    from PySide6.QtTest import QTest as _QTest
    now = _QDate.currentDate()
    cur_m = f"{now.year()}年 {now.month()}月"
    win._day_mode_seg.set_current_index(2)   # 切到完成图
    app.processEvents()
    check("heat mode switched on", win._day_stack.currentIndex() == 2
          and db.get_setting("day_mode") == "heat")
    grid = win._heat._grid
    check("heat shows current month",
          (grid._year, grid._month) == (now.year(), now.month())
          and win._heat._range_label.text() == cur_m,
          win._heat._range_label.text())
    check("heat grid has today stats",
          grid._stats.get(t_key) == (1, 2), str(grid._stats.get(t_key)))
    rect = grid._cell_rect(now)
    check("heat today cell geometry exists", rect is not None)
    center = rect.center().toPoint()
    _QTest.mouseClick(grid, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier, center)
    app.processEvents()
    check("heat click opens single day", win._day_stack.currentIndex() == 0
          and win._day_mode == "single"
          and win._selected_date.toString("yyyy-MM-dd") == t_key)
    stats_txt = win._heat._stats_label.text()
    check("heat stats label", "共完成" in stats_txt and "今日" in stats_txt
          and "全部完成" in stats_txt, stats_txt)
    check("heat newer/回本月 disabled at current month",
          not win._heat._btn_newer.isEnabled()
          and not win._heat._btn_today_month.isEnabled())
    # 前后月份跳转
    win._heat._btn_older.click()
    app.processEvents()
    prev = now.addMonths(-1)
    check("heat older -> previous month",
          (grid._year, grid._month) == (prev.year(), prev.month())
          and win._heat._btn_newer.isEnabled()
          and win._heat._btn_today_month.isEnabled()
          and f"{prev.year()}年 {prev.month()}月" in win._heat._range_label.text(),
          win._heat._range_label.text())
    win._heat._btn_newer.click()
    app.processEvents()
    check("heat newer back to current month",
          (grid._year, grid._month) == (now.year(), now.month()))
    # 表格随窗口变大：格子边长增大
    win._day_mode_seg.set_current_index(2)   # 切回完成图（可见才参与布局）
    app.processEvents()
    grid0 = grid._cell
    win.resize(1000, 640)
    app.processEvents()
    app.processEvents()     # 让 resize 后的延迟 relayout 执行
    check("heat grid grows with window", grid._cell > grid0,
          f"cell {grid0}->{grid._cell}")

    # 回归：reload 后不应有“游离成顶层窗口”的行/标签（曾在切天时闪现弹框）
    for _ in range(3):
        win._shift_day(1)
        win._shift_day(-1)
    app.processEvents()
    stray = [w.metaObject().className() for w in app.topLevelWidgets()
             if w.isVisible() and w is not win
             and ('TaskRow' in w.metaObject().className()
                  or 'QLabel' in w.metaObject().className())]
    check("no stray top-level rows/labels after day switches", not stray, str(stray))

    # 数据持久化：重开数据库仍在
    db.close()
    db2 = Database(db_path)
    check("persist across reopen", len(db2.list_tasks("long")) == 5
          and db2.counts("long")[1] == 5)
    check("settings persist", db2.get_setting("hello") == "world")
    db2.close()

    print("-" * 46)
    if FAILURES:
        print(f"SMOKE FAILED: {len(FAILURES)} 项失败")
        for f in FAILURES:
            print("  ✗", f)
        return 1
    print("SMOKE PASSED: 全部通过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:                    # noqa: BLE001
        traceback.print_exc()
        code = 2
    finally:
        app.quit()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
