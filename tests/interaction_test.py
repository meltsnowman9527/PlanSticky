"""交互层回归测试（QTest 合成真实鼠标事件）。

覆盖两个已修复的 bug：
1. 置顶：点击后窗口保持可见、WindowStaysOnTopHint 真正生效；
2. 拖动排序 vs 拖动窗口 的事件冒泡冲突：
   - 点任务文字 -> 进入编辑；
   - 按住任务文字拖动 -> 发出排序拖动请求；
   - 按住 ⋮⋮ 把手拖动 -> 发出排序拖动请求；
   - 在行内空白/文字/把手上按下 -> 事件被行内控件吞掉，不再冒泡成“拖动窗口”。

运行：.venv\\Scripts\\python.exe tests\\interaction_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
_TMP = tempfile.mkdtemp(prefix="plansticky_interact_")
os.environ["PLANSTICKY_DATA_DIR"] = _TMP
os.environ["PLANSTICKY_SINGLE"] = "0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

app = QApplication([])

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    from plansticky import config
    from plansticky.database import Database
    from plansticky.main_window import MainWindow
    from plansticky.task_row import TaskRow
    from plansticky.theme import ThemeManager

    TOP = Qt.WindowType.WindowStaysOnTopHint

    # ============================================================ 1) 置顶
    db = Database(config.db_path())
    theme = ThemeManager(db)
    win = MainWindow(db, theme)
    win.show()
    app.processEvents()
    win.set_pinned(True)
    app.processEvents()
    check("pin: window stays visible", win.isVisible())
    check("pin: flag applied", bool(win.windowFlags() & TOP))
    check("pin: db persisted", db.get_setting_bool("window_pinned") is True)
    win.set_pinned(False)
    app.processEvents()
    check("unpin: window stays visible", win.isVisible())
    check("unpin: flag cleared", not bool(win.windowFlags() & TOP))

    # 通过按钮点击再走一遍（真实入口）
    win._btn_pin.click()
    app.processEvents()
    check("pin via button: visible & flagged",
          win.isVisible() and bool(win.windowFlags() & TOP))
    win._btn_pin.click()
    app.processEvents()
    check("unpin via button: visible & cleared",
          win.isVisible() and not bool(win.windowFlags() & TOP))

    # ============================================================ 2) 行交互
    host = QWidget()
    lay = QVBoxLayout(host)
    row = TaskRow(1, "拖动排序测试任务", False)
    lay.addWidget(row)
    host.resize(320, 120)
    host.show()
    app.processEvents()
    label = row._cell.label
    handle = row._handle

    drags: list[QPoint] = []
    row.dragRequested.connect(drags.append)

    # 2a. 单击文字 -> 进入编辑（不是拖窗口）
    QTest.mouseClick(label, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(40, label.height() // 2))
    app.processEvents()
    check("click label enters edit", row._cell.is_editing())
    check("click label did not drag", len(drags) == 0, str(drags))
    row._on_cancel()   # Esc 等价：退出编辑

    # 2b. 按住文字拖过阈值 -> 发出拖动排序请求，且松手不进入编辑
    p0 = QPoint(40, label.height() // 2)
    QTest.mousePress(label, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, p0)
    QTest.mouseMove(label, QPoint(p0.x() + 80, p0.y()))
    QTest.mouseRelease(label, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, QPoint(p0.x() + 80, p0.y()))
    app.processEvents()
    check("drag label emits dragRequested", len(drags) == 1, str(drags))
    check("drag label does not enter edit", not row._cell.is_editing())

    # 2c. 按住 ⋮⋮ 把手拖动 -> 发出拖动排序请求
    QTest.mousePress(handle, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     QPoint(handle.width() // 2, handle.height() // 2))
    QTest.mouseMove(handle, QPoint(handle.width() // 2 + 60, handle.height() // 2))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier,
                       QPoint(handle.width() // 2 + 60, handle.height() // 2))
    app.processEvents()
    check("drag handle emits dragRequested", len(drags) == 2, str(drags))

    # 2d. 复选框点击正常 toggle（行内控件未被行吞掉）
    QTest.mouseClick(row._check, Qt.MouseButton.LeftButton)
    app.processEvents()
    check("checkbox toggles done", row.done is True)

    # 2e. 冒泡检查：行内控件吞掉按下事件 -> 不冒泡成“拖动窗口”。
    # 方法：把按下事件直接投给行内控件；若控件不 accept，Qt 会把忽略的事件
    # 沿父链冒泡到主窗口（用“发给空白页控件”作对照，证明冒泡链路真实存在）。
    from PySide6.QtCore import QObject

    class PressCounter(QObject):
        def __init__(self, target):
            super().__init__()
            self.n = 0
            target.installEventFilter(self)

        def eventFilter(self, obj, ev) -> bool:   # noqa: N802
            if ev.type() == QEvent.Type.MouseButtonPress:
                self.n += 1
            return False

    # 主窗口里加一条今天的任务
    from PySide6.QtCore import QDate
    day_key = QDate.currentDate().toString("yyyy-MM-dd")
    db.add_task("day", "冒泡测试", day_key)
    win.show_today()
    win._day_view.reload()
    app.processEvents()
    prow = win._day_view._pending_rows[0]
    counter = PressCounter(win)

    # 对照：发给“什么都不处理”的页容器 -> 必然冒泡到主窗口（证明测试有效）
    QTest.mousePress(win._day_page, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
    QTest.mouseRelease(win._day_page, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
    app.processEvents()
    control = counter.n
    check("control: ignore-event bubbles to window", control == 1,
          f"window presses={control}")

    # 行内文字按下 -> 被文字标签吞掉
    lp = QPoint(20, prow._cell.label.height() // 2)
    QTest.mousePress(prow._cell.label, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, lp)
    QTest.mouseRelease(prow._cell.label, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, lp)
    app.processEvents()
    check("label press not bubbled to window", counter.n == control,
          f"window presses={counter.n}")
    # 行内空白（左缘）按下 -> 被行吞掉
    QTest.mousePress(prow, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(2, 18))
    QTest.mouseRelease(prow, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, QPoint(2, 18))
    app.processEvents()
    check("row blank press not bubbled to window", counter.n == control,
          f"window presses={counter.n}")
    # 拖拽把手按下 -> 被把手吞掉
    hp = QPoint(prow._handle.width() // 2, prow._handle.height() // 2)
    QTest.mousePress(prow._handle, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, hp)
    QTest.mouseRelease(prow._handle, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, hp)
    app.processEvents()
    check("handle press not bubbled to window", counter.n == control,
          f"window presses={counter.n}")

    win.close()
    db.close()
    print("-" * 46)
    if FAILURES:
        print(f"INTERACTION FAILED: {len(FAILURES)} 项失败")
        for f in FAILURES:
            print("  ✗", f)
        return 1
    print("INTERACTION PASSED: 全部通过")
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
