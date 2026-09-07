"""拖拽排序落点回归测试（合成拖放事件直接投给 viewport）。

背景：真实 QDrag 在离屏平台不派发事件，因此用直接构造 QDragEnterEvent /
QDragMoveEvent / QDropEvent 投递，模拟平台把事件交给 viewport 后的处理链路
（dragEnter/dragMove/drop 处理器 + 坐标换算 + 插入语义 + 落库）。

覆盖：
  A. 顶部插入   B. 中部插入(向下)   C. 底部插入   D. 滚动到中段后的插入

运行：.venv\\Scripts\\python.exe tests\\drag_drop_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
_TMP = tempfile.mkdtemp(prefix="plansticky_dnd_")
os.environ["PLANSTICKY_DATA_DIR"] = _TMP
os.environ["PLANSTICKY_SINGLE"] = "0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QByteArray, QDate, QMimeData, QPoint, Qt
from PySide6.QtGui import (QDragEnterEvent, QDragMoveEvent, QDropEvent)
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

app = QApplication([])

from plansticky.database import Database          # noqa: E402
from plansticky.task_list import MIME_FORMAT, TaskListView  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    db = Database(os.path.join(_TMP, "t.db"))
    day = QDate.currentDate().toString("yyyy-MM-dd")

    host = QWidget()
    lay = QVBoxLayout(host)
    view = TaskListView(db, "day", day)
    lay.addWidget(view)
    host.resize(340, 560)
    host.show()
    app.processEvents()

    def drag_to(drag_row, vp_pos) -> tuple[int | None, bool]:
        """模拟拖拽：drag_row 开始，光标在 viewport 坐标 vp_pos 释放。"""
        view._drag_row = drag_row
        view._drag_block = (view._pending_rows if drag_row in view._pending_rows
                            else view._done_rows)
        md = QMimeData()
        md.setData(MIME_FORMAT,
                   QByteArray(json.dumps({"id": drag_row.task_id}).encode()))
        QApplication.sendEvent(
            view.viewport(),
            QDragEnterEvent(QPoint(vp_pos), Qt.DropAction.MoveAction, md,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier))
        QApplication.sendEvent(
            view.viewport(),
            QDragMoveEvent(QPoint(vp_pos), Qt.DropAction.MoveAction, md,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier))
        app.processEvents()
        idx = view._last_idx
        ind = view._indicator.isVisible()
        QApplication.sendEvent(
            view.viewport(),
            QDropEvent(QPoint(vp_pos), Qt.DropAction.MoveAction, md,
                       Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier))
        app.processEvents()
        view._drag_row = None
        view._drag_block = None
        return idx, ind

    def fresh(n: int) -> list:
        for t in db.list_tasks("day", day):
            db.delete_task(t["id"])
        for i in range(n):
            db.add_task("day", f"任务{i + 1}", day)
        view.reload()
        view.verticalScrollBar().setValue(0)
        app.processEvents()
        return list(view._pending_rows)

    def expect_after(src_idx: int, gap_idx: int, n: int) -> list[str]:
        """复刻 dropEvent 的插入语义，得出期望顺序。"""
        ids = list(range(n))
        old = src_idx
        idx = gap_idx
        if old < idx:
            idx -= 1
        moved = ids.pop(old)
        ids.insert(idx, moved)
        return [f"任务{i + 1}" for i in ids]

    # ---- A/B/C：不滚动，5 行（行顶 content y=2,44,86,128,170，行高36）----
    names5 = [f"任务{i + 1}" for i in range(5)]

    rows = fresh(5)
    idx, ind = drag_to(rows[3], QPoint(150, 8))     # 顶缝 g0≈0
    order = [r.text_value() for r in view._pending_rows]
    check("A top insert", idx == 0 and ind and order == expect_after(3, 0, 5),
          f"idx={idx} ind={ind} {order}")

    rows = fresh(5)
    idx, ind = drag_to(rows[0], QPoint(150, 150))   # 第4行附近
    order = [r.text_value() for r in view._pending_rows]
    check("B middle insert", ind and order == expect_after(0, 4, 5),
          f"idx={idx} ind={ind} {order}")

    rows = fresh(5)
    idx, ind = drag_to(rows[1], QPoint(150, 230))   # 列表末尾下方空白
    order = [r.text_value() for r in view._pending_rows]
    check("C bottom insert", ind and order == expect_after(1, 5, 5),
          f"idx={idx} ind={ind} {order}")

    # ---- D：滚到中段再插入（20 条任务，content 高度超过 viewport）----
    rows = fresh(20)
    sb = view.verticalScrollBar()
    sb.setValue(sb.maximum() // 2)                  # 滚到中段
    app.processEvents()
    src = rows[4]
    # 挑一个“可视”的目标行：其 viewport y 在 [0, vp.h) 内
    target = None
    for r in rows[10:18]:
        vy = r.mapTo(view.viewport(), QPoint(0, 0)).y()
        if 40 <= vy <= view.viewport().height() - 40:
            target, ty = r, vy
            break
    if target is None:
        check("D scroll target found", False)
    else:
        gap_idx = view._compute_gap(view._pending_rows,
                                    view._to_content_y(ty))[0]
        idx, ind = drag_to(src, QPoint(150, ty + 10))
        order = [r.text_value() for r in view._pending_rows]
        check("D scrolled middle insert",
              ind and order == expect_after(4, gap_idx, 20),
              f"idx={idx} gap={gap_idx} ty={ty} ind={ind}")

    host.close()
    db.close()
    print("-" * 46)
    if FAILURES:
        print(f"DRAGDROP FAILED: {len(FAILURES)} 项失败")
        for f in FAILURES:
            print("  ✗", f)
        return 1
    print("DRAGDROP PASSED: 全部通过")
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
