"""可滚动计划列表。

- 分区显示：未完成在上、已完成在下（完成区带“已完成”小标题）；
- 标题与行数：任务很多时纵向滚动（横向滚动条禁用）；
- 拖拽排序：抓住行尾的 ⋮⋮ 把手拖动，出现插入指示线，松手即持久化；
- 任务状态切换/删除/编辑后自动落库。

删除行内编辑器在 task_row 内完成；删除走这里统一确认。
"""
from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter
from PySide6.QtWidgets import (QLabel, QMessageBox, QScrollArea,
                               QVBoxLayout, QWidget)

from plansticky.database import KIND_DAY, KIND_LONG, Database
from plansticky.task_row import TaskRow
from plansticky.theme import color

MIME_FORMAT = "application/x-plansticky-row"

SPACING = 6          # 行间距（与计算插入指示线位置相关）
SCROLL_STEP = 16     # 拖动到边缘时的自动滚动步长


class _DropIndicator(QWidget):
    """拖拽时的插入位置指示线。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedHeight(3)
        self.hide()

    def paintEvent(self, e) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(color("accent"))
        c.setAlpha(220)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(4, 0, self.width() - 8, 3, 1.5, 1.5)
        p.end()

    def place(self, y: int, width: int) -> None:
        self.setGeometry(0, y - 1, width, 3)
        self.show()
        self.raise_()


class TaskListView(QScrollArea):
    """长期计划列表或某一天的短期计划列表（kind/day 决定数据区域）。"""

    countChanged = Signal(int, int)   # (已完成, 总数)

    def __init__(self, db: Database, kind: str, day: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._kind = kind
        self._day = day
        self._hide_done = False

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.viewport().setAcceptDrops(True)

        self._content = QWidget(self)
        self._content.setObjectName("listContent")
        self.setWidget(self._content)
        self._vbox = QVBoxLayout(self._content)
        self._vbox.setContentsMargins(2, 2, 4, 2)
        self._vbox.setSpacing(SPACING)

        # 空态提示文案；提示控件本身每次 reload 按需新建（见 reload），
        # 不能作为常驻子控件复用——否则会在下次“清空布局”时被 deleteLater 删掉，
        # 再次切到空列表再 addWidget 就会触发 “C++ object already deleted” 崩溃。
        self._empty_text = (
            "这一天还没有计划，点击下方添加" if kind == KIND_DAY
            else "还没有长期计划，点击下方添加"
        )
        self._empty_hint: QLabel | None = None

        self._pending_rows: list[TaskRow] = []
        self._done_rows: list[TaskRow] = []
        self._done_label: QLabel | None = None

        # 拖拽状态
        self._drag_row: TaskRow | None = None
        self._drag_block: list[TaskRow] | None = None
        self._indicator = _DropIndicator(self._content)

        self.reload(keep_scroll=False)

    # ============================================================ 对外接口
    @property
    def kind(self) -> str:
        return self._kind

    @property
    def day(self) -> str | None:
        return self._day

    def is_long(self) -> bool:
        return self._kind == KIND_LONG

    def set_day(self, day: str) -> None:
        """短期列表：切换日期（整页数据随之更换）。"""
        if self._kind != KIND_DAY:
            return
        if day != self._day:
            self._day = day
            self.reload(keep_scroll=False)

    def set_hide_done(self, hide: bool) -> None:
        if hide != self._hide_done:
            self._hide_done = hide
            self.reload(keep_scroll=True)

    def finish_editing_rows(self) -> None:
        """提交/取消所有正在行内编辑的内容（窗口隐藏前调用）。"""
        for r in list(self._pending_rows) + list(self._done_rows):
            r.finish_editing()

    def reload(self, keep_scroll: bool = True) -> None:
        """从数据库重建整列。所有数据变更后都走这里，保证 UI == DB。"""
        scroll = self.verticalScrollBar()
        ratio = 0.0
        if keep_scroll and scroll.maximum() > 0:
            ratio = scroll.value() / scroll.maximum()

        tasks = self._db.list_tasks(self._kind, self._day)
        pending = [t for t in tasks if not t["done"]]
        done = [t for t in tasks if t["done"]]

        # ---- 清空旧内容
        self.setUpdatesEnabled(False)
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                # 关键：不能 setParent(None)——那会让它瞬间变成“无父级的顶层窗口”，
                # 在 deleteLater 真正删除前于屏幕上闪一下（切天时见到的神秘弹框）。
                # 先 hide() 立即使其不可见，再排队删除，且保持父级不变。
                w.hide()
                w.deleteLater()
        self._pending_rows = []
        self._done_rows = []
        self._done_label = None

        # ---- 重建
        if not tasks:
            hint = QLabel(self._content)
            hint.setObjectName("emptyHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            hint.setText(self._empty_text)
            hint.setFixedWidth(200)
            self._vbox.addStretch(1)
            self._vbox.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)
            self._vbox.addStretch(2)
            self._empty_hint = hint
        else:
            self._empty_hint = None
            for t in pending:
                row = self._make_row(t)
                self._pending_rows.append(row)
                self._vbox.addWidget(row)
            if done and not self._hide_done:
                label = QLabel(self._content)
                label.setObjectName("sectionLabel")
                label.setText(f"已完成 · {len(done)}")
                self._vbox.addWidget(label)
                self._done_label = label
                for t in done:
                    row = self._make_row(t)
                    self._done_rows.append(row)
                    self._vbox.addWidget(row)
            self._vbox.addStretch(1)

        self.setUpdatesEnabled(True)
        self._hide_indicator()

        # 恢复滚动位置（内容高度变化不大时基本无感）
        if keep_scroll:
            from PySide6.QtCore import QTimer
            sb = self.verticalScrollBar()
            QTimer.singleShot(0, lambda: self._restore_scroll(sb, ratio))

        self.countChanged.emit(len(done), len(tasks))

    def _restore_scroll(self, sb, ratio: float) -> None:
        if sb.maximum() > 0:
            sb.setValue(round(ratio * sb.maximum()))

    # ------------------------------------------------------------ 内部构建
    def _make_row(self, task: dict) -> TaskRow:
        row = TaskRow(task["id"], task["content"], bool(task["done"]), self._content)
        row.toggled.connect(self._on_row_toggled)
        row.editSaved.connect(self._on_row_edited)
        row.deleteRequested.connect(self._on_row_delete)
        row.dragRequested.connect(
            lambda pos, r=row: self._start_drag(r, pos))
        return row

    def _on_row_toggled(self, task_id: int, checked: bool) -> None:
        self._db.set_done(task_id, checked)
        self.reload(keep_scroll=True)

    def _on_row_edited(self, task_id: int, new_text: str) -> None:
        self._db.update_content(task_id, new_text)

    def _on_row_delete(self, task_id: int) -> None:
        text = ""
        for t in self._db.list_tasks(self._kind, self._day):
            if t["id"] == task_id:
                text = t["content"]
                break
        box = QMessageBox(QMessageBox.Icon.Question, "删除计划",
                          f"确定删除「{text}」吗？", parent=self.window())
        btn_del = box.addButton("删除", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_cancel)
        box.exec()
        if box.clickedButton() is btn_del:
            self._db.delete_task(task_id)
            self.reload(keep_scroll=True)

    # ============================================================ 拖拽排序
    def _block_of(self, row: TaskRow) -> list[TaskRow] | None:
        if row in self._pending_rows:
            return self._pending_rows
        if row in self._done_rows:
            return self._done_rows
        return None

    def _start_drag(self, row: TaskRow, global_press: QPoint) -> None:
        block = self._block_of(row)
        if block is None:
            return
        drag = QDrag(self)
        md = QMimeData()
        md.setData(MIME_FORMAT, QByteArray(
            json.dumps({"id": row.task_id}).encode("utf-8")))
        drag.setMimeData(md)
        pix = row.grab()
        if pix.width() > 320:
            pix = pix.scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
        drag.setPixmap(pix)
        drag.setHotSpot(row.mapFromGlobal(global_press))
        self._drag_row = row
        self._drag_block = block
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_row = None
        self._drag_block = None
        self._hide_indicator()

    # ---- 拖放事件（目标 = 本滚动区 viewport）----
    def _valid_drag(self, mime) -> bool:
        return (self._drag_row is not None and self._drag_block is not None
                and mime is not None and mime.hasFormat(MIME_FORMAT))

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if self._valid_drag(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:   # noqa: N802
        if not self._valid_drag(e.mimeData()):
            e.ignore()
            return
        pos = e.position().toPoint()
        cy = self._viewport_to_content(pos).y()
        gap = self._compute_gap(self._drag_block, cy)
        if gap is None:
            self._hide_indicator()
        else:
            idx, y = gap
            self._indicator.place(y, self._content.width())
            self._last_idx = idx
        # 自动滚动：靠近上下边缘
        sb = self.verticalScrollBar()
        vh = self.viewport().height()
        if pos.y() < 36:
            sb.setValue(sb.value() - SCROLL_STEP)
        elif pos.y() > vh - 36:
            sb.setValue(sb.value() + SCROLL_STEP)
        e.acceptProposedAction()

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._hide_indicator()
        e.accept()

    def dropEvent(self, e) -> None:       # noqa: N802
        if not self._valid_drag(e.mimeData()):
            e.ignore()
            return
        self._hide_indicator()
        block = self._drag_block
        row = self._drag_row
        if not block or row is None:
            e.ignore()
            return
        pos = e.position().toPoint()
        cy = self._viewport_to_content(pos).y()
        gap = self._compute_gap(block, cy)
        if gap is None:
            e.ignore()
            return
        idx, _ = gap
        ids = [r.task_id for r in block]
        old = ids.index(row.task_id)
        if old < idx:
            idx -= 1
        new_ids = [i for i in ids if i != row.task_id]
        new_ids.insert(idx, row.task_id)
        if new_ids != ids:
            self._db.reorder(self._kind, self._day, new_ids)
            self.reload(keep_scroll=True)
        e.acceptProposedAction()

    def _hide_indicator(self) -> None:
        self._indicator.hide()

    # ---- 几何计算 ----
    def _content_offset(self) -> int:
        """content 原点在 viewport 坐标系里的 y（未滚动=0，下滚为负）。

        注意：不能用 viewport.mapTo(content) 反向换算——它在列表重建后
        会带一个固定偏移（离屏实测 +13px），把落点整体推后导致“只能拖到底部”。
        content->viewport 是直接父子换算，可靠；滚动偏移换算用减法完成。
        """
        return self._content.mapTo(self.viewport(), QPoint(0, 0)).y()

    def _to_content_y(self, viewport_y: int) -> int:
        return viewport_y - self._content_offset()

    def _viewport_to_content(self, pos: QPoint) -> QPoint:
        return QPoint(pos.x(), self._to_content_y(pos.y()))

    def _compute_gap(self, rows: list[TaskRow], cy: int):
        """返回 (插入位置 idx, 指示线 y) 或 None（光标不在有效落点）。"""
        if not rows:
            return None
        coords = [r.mapTo(self._content, QPoint(0, 0)) for r in rows]
        tops = [c.y() for c in coords]
        first_top = tops[0]
        last_bottom = tops[-1] + rows[-1].height()

        # 该分区块是否可视的最后一块（决定末尾落点向下放宽多少）
        if self._done_rows:
            is_last_block = rows is self._done_rows
        else:
            is_last_block = rows is self._pending_rows

        # 候选插入缝
        gaps: list[tuple[int, int]] = []   # (idx, y)
        gaps.append((0, max(0, first_top - SPACING // 2)))
        for i in range(1, len(rows)):
            mid = (tops[i - 1] + rows[i - 1].height() + tops[i]) // 2
            gaps.append((i, mid))
        gaps.append((len(rows), last_bottom + SPACING // 2))

        # 合法区间：本块上下略微外扩
        top_limit = first_top - SPACING
        if is_last_block:
            viewport_bottom = self.viewport().height() - self._content_offset()
            bottom_limit = max(last_bottom + SPACING, viewport_bottom)
        else:
            bottom_limit = last_bottom + SPACING
        if cy < top_limit or cy > bottom_limit:
            return None

        best = min(gaps, key=lambda g: abs(g[1] - cy))
        idx, y = best
        if y < 0 or y > self._content.height():
            y = min(max(y, 0), self._content.height())
        return idx, y
