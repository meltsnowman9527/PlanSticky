"""多日列表视图（列表模式）。

按日期从上到下连续排列（今天往前 7 天、往后 31 天；范围外凡是有任务
的日期也会追加进来）。每个日期一行标题 + 该天计划：

    ──────────────────────────
    今天 · 9月7日 周一 · 1/2        <- 点击标题进入该天编辑
        ☑ 写周报
        ☐ 买牛奶
    ──────────────────────────
    9月8日 周二                    <- 没有计划只显示日期
    ──────────────────────────

列表内行支持：勾选完成/取消、行内编辑、⋯ 删除（均有确认）。拖动排序
在多日视图里不提供（把手隐藏），排序请进单日视图。

点击任意日期标题 -> dateActivated(YYYY-MM-DD)，由主窗口切回“单日”并打开该天。
"""
from __future__ import annotations

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from plansticky.database import Database
from plansticky.task_row import TaskRow

LOOKBACK_DAYS = 7      # 从今天往前显示的日期数
LOOKAHEAD_DAYS = 31    # 从今天往后显示的日期数


class DayListView(QScrollArea):
    """把每一天当成一个分节的纵向滚动列表。"""

    dateActivated = Signal(str)   # YYYY-MM-DD：点击日期标题

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        self._content = QWidget(self)
        self._content.setObjectName("listContent")
        self.setWidget(self._content)
        self._vbox = QVBoxLayout(self._content)
        self._vbox.setContentsMargins(2, 6, 6, 8)
        self._vbox.setSpacing(10)

        self._empty_hint = QLabel(self._content)
        self._empty_hint.setObjectName("emptyHint")
        self._empty_hint.setText("还没有任何日计划")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setVisible(False)

        self._day_headers: dict[str, QPushButton] = {}   # key -> 标题按钮
        self._pending: dict[str, list[TaskRow]] = {}     # key -> 行列表（便于测试/刷新）
        self._scroll_ratio = 0.0

    # ============================================================ 对外接口
    def reload(self, scroll_to_today: bool = False) -> None:
        """重建整页（所有日期 + 每天的计划）。scroll_to_today：跳到今天分节。"""
        sb = self.verticalScrollBar()
        if not scroll_to_today and sb.maximum() > 0:
            self._scroll_ratio = sb.value() / sb.maximum()
        else:
            self._scroll_ratio = 0.0

        self.setUpdatesEnabled(False)
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                # 先 hide 再 deleteLater：避免短暂成为“无父级顶层窗口”而闪现
                w.hide()
                w.deleteLater()
        self._day_headers = {}
        self._pending = {}

        today = QDate.currentDate()
        window = {today.addDays(i).toString("yyyy-MM-dd")
                  for i in range(-LOOKBACK_DAYS, LOOKAHEAD_DAYS + 1)}
        days = sorted(window | set(self._db.days_with_tasks()))
        if not days:
            self._vbox.addStretch(1)
            self._vbox.addWidget(self._empty_hint, 0,
                                 Qt.AlignmentFlag.AlignHCenter)
            self._vbox.addStretch(2)

        for key in days:
            date = QDate.fromString(key, "yyyy-MM-dd")
            tasks = self._db.list_tasks("day", key)
            header = self._make_day_header(date, tasks)
            self._vbox.addWidget(header)
            self._day_headers[key] = header
            if tasks:
                box = QWidget(self._content)
                bv = QVBoxLayout(box)
                bv.setContentsMargins(14, 0, 0, 0)
                bv.setSpacing(2)
                rows: list[TaskRow] = []
                for t in tasks:
                    row = TaskRow(t["id"], t["content"], bool(t["done"]),
                                  box, show_handle=False)
                    row.toggled.connect(self._on_row_toggled)
                    row.editSaved.connect(self._on_row_edited)
                    row.deleteRequested.connect(self._on_row_delete)
                    bv.addWidget(row)
                    rows.append(row)
                self._vbox.addWidget(box)
                self._pending[key] = rows

        self.setUpdatesEnabled(True)

        from PySide6.QtCore import QTimer
        target_ratio = self._scroll_ratio
        if scroll_to_today:
            target_ratio = 0.0
            today_key = today.toString("yyyy-MM-dd")
            header = self._day_headers.get(today_key)
            QTimer.singleShot(
                0, lambda: self._scroll_header_into_view(header, today_key))
        else:
            sb2 = self.verticalScrollBar()
            QTimer.singleShot(0, lambda: self._restore_ratio(sb2, target_ratio))

    def _scroll_header_into_view(self, header, today_key: str) -> None:
        if header is None:
            return
        y = header.mapTo(self._content, header.rect().topLeft()).y()
        self.verticalScrollBar().setValue(max(0, y - 2))

    def _restore_ratio(self, sb, ratio: float) -> None:
        if sb.maximum() > 0:
            sb.setValue(round(ratio * sb.maximum()))

    def finish_editing_rows(self) -> None:
        """提交/取消正在行内编辑的内容（窗口隐藏/切走前调用）。"""
        for rows in self._pending.values():
            for r in rows:
                r.finish_editing()

    # ============================================================ 内部
    def _make_day_header(self, date: QDate, tasks: list) -> QPushButton:
        done = sum(1 for t in tasks if t["done"])
        total = len(tasks)
        wd = "周" + "一二三四五六日"[date.dayOfWeek() - 1]
        is_today = date == QDate.currentDate()
        if is_today:
            label = f"今天 · {date.month()}月{date.day()}日 {wd}"
        else:
            label = f"{date.month()}月{date.day()}日 {wd}"
        if total:
            label += f" · {done}/{total}"
        btn = QPushButton(label, self._content)
        btn.setObjectName("listDayHead")
        btn.setProperty("today", "true" if is_today else "false")
        btn.setProperty("empty", "true" if not total else "false")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("点击进入该天编辑"
                       if not is_today else "点击进入今天编辑")
        key = date.toString("yyyy-MM-dd")
        btn.clicked.connect(lambda _=False, k=key: self.dateActivated.emit(k))
        return btn

    def _on_row_toggled(self, task_id: int, checked: bool) -> None:
        self._db.set_done(task_id, checked)
        self.reload()

    def _on_row_edited(self, task_id: int, new_text: str) -> None:
        self._db.update_content(task_id, new_text)

    def _confirm_delete(self, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, "删除计划",
                          f"确定删除「{text}」吗？", parent=self.window())
        btn_del = box.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_cancel)
        box.exec()
        return box.clickedButton() is btn_del

    def _on_row_delete(self, task_id: int) -> None:
        for key, rows in self._pending.items():
            for r in rows:
                if r.task_id == task_id:
                    tasks = self._db.list_tasks("day", key)
                    text = next((t["content"] for t in tasks
                                 if t["id"] == task_id), "")
                    if self._confirm_delete(text):
                        self._db.delete_task(task_id)
                        self.reload()
                    return
