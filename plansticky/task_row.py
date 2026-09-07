"""单条计划行。

布局： [复选框] [内容(点击进入编辑 / 双击意义同单击)] [拖拽把手] [⋯ 菜单]

交互：
- 复选框        -> 完成/取消完成（列表负责把行移入对应分区）
- 内容单击      -> 就地变成输入框编辑；Enter 保存、Esc 取消、失焦自动保存
- 拖拽把手      -> 按住拖动排序（真正的 QDrag 由 TaskListView 发起）
- ⋯ 菜单        -> 编辑 / 删除

视觉：整行 hover 圆角底色；完成任务的文字由 TextLabel 自绘（灰 + 删除线）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QLineEdit, QMenu, QPushButton,
                               QWidget)

from plansticky import icons
from plansticky.theme import color
from plansticky.ui_common import CheckBox, DragHandle, TextLabel

ROW_HEIGHT = 36


# ------------------------------------------------------------------ 编辑器
class RowEditor(QLineEdit):
    """行内编辑输入框。Enter=保存、Esc=取消、失焦=保存（非空时）。"""

    commitText = Signal(str)
    cancelEdit = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("rowEditor")
        self._skip_focus = False

    def keyPressEvent(self, e) -> None:   # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            e.accept()
            self._skip_focus = True
            self.cancelEdit.emit()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            e.accept()
            t = self.text().strip()
            if t:
                self.commitText.emit(t)
            else:
                self._skip_focus = True
                self.cancelEdit.emit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:   # noqa: N802
        if not self._skip_focus and self.isVisible():
            t = self.text().strip()
            if t:
                self.commitText.emit(t)
            else:
                self.cancelEdit.emit()
        super().focusOutEvent(e)


# -------------------------------------------------------------- 内容单元格
class ContentCell(QWidget):
    """标签 / 输入框切换显示的容器（两者同几何，手动布局）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._label = TextLabel(parent=self)
        self._label.setCursor(Qt.CursorShape.IBeamCursor)
        self._label.setToolTip("单击编辑 · 按住文字拖动排序")
        self._editor = RowEditor(parent=self)
        self._editor.hide()

    # ---- 供 TaskRow 转发 ----
    @property
    def label(self) -> TextLabel:
        return self._label

    @property
    def editor(self) -> RowEditor:
        return self._editor

    def resizeEvent(self, e) -> None:     # noqa: N802
        r = self.contentsRect()
        self._label.setGeometry(r)
        self._editor.setGeometry(r)
        super().resizeEvent(e)

    def is_editing(self) -> bool:
        return self._editor.isVisible()

    def begin_edit(self, text: str) -> None:
        self._editor._skip_focus = False
        self._editor.setText(text)
        self._label.hide()
        self._editor.show()
        self._editor.raise_()
        self._editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self._editor.selectAll()

    def end_edit(self) -> None:
        self._editor.hide()
        self._label.show()


# ------------------------------------------------------------ hover 追踪器
class _RowHover(QObject):
    """给行及其所有子控件装事件过滤器，统一维护“鼠标是否在行上”。"""

    def __init__(self, row: "TaskRow"):
        super().__init__(row)
        self._row = row
        self._current = False
        for w in row._hover_targets():
            w.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:   # noqa: N802
        if ev.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            hovered = self._row._cursor_inside()
            if hovered != self._current:
                self._current = hovered
                self._row._set_hover(hovered)
        return False


# ------------------------------------------------------------------ 任务行
class TaskRow(QWidget):
    """一条计划（长期或某天的短期）。"""

    toggled = Signal(int, bool)        # (task_id, done)
    editSaved = Signal(int, str)       # (task_id, 新内容) —— 已修改才发
    deleteRequested = Signal(int)
    dragRequested = Signal(QPoint)     # 全局坐标（按下点），转发自把手

    def __init__(self, task_id: int, text: str, done: bool,
                 parent: QWidget | None = None, show_handle: bool = True):
        super().__init__(parent)
        self._task_id = task_id
        self._text = text
        self._done = done
        self._hovered = False
        self._show_handle = show_handle
        self.setFixedHeight(ROW_HEIGHT)
        self.setMouseTracking(True)

        from PySide6.QtWidgets import QHBoxLayout
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 2, 0)
        lay.setSpacing(6)

        # 复选框
        self._check = CheckBox(self)
        self._check.setChecked(done)  # 先设状态再接信号，避免重建时误触发
        self._check.toggled.connect(self._on_check_toggled)
        lay.addWidget(self._check, 0, Qt.AlignmentFlag.AlignVCenter)

        # 内容
        self._cell = ContentCell(self)
        self._cell.label.setText(text)
        self._cell.label.set_done(done)
        self._cell.label.clicked.connect(self.start_edit)
        # 按住文字本身拖动 = 拖动排序（把手之外的常用姿势）
        self._cell.label.dragStart.connect(self.dragRequested)
        self._cell.editor.commitText.connect(self._on_commit)
        self._cell.editor.cancelEdit.connect(self._on_cancel)
        lay.addWidget(self._cell, 1)

        # 拖拽把手（列表视图等不需要排序的场景可隐藏）
        self._handle: DragHandle | None = None
        if show_handle:
            self._handle = DragHandle(self)
            self._handle.dragRequested.connect(self.dragRequested)
            lay.addWidget(self._handle, 0, Qt.AlignmentFlag.AlignVCenter)

        # ⋯ 菜单
        self._more = QPushButton(self)
        self._more.setProperty("cls", "icon")
        self._more.setFixedSize(24, 24)
        if icons.has_mdl2_font():
            self._more.setFont(icons.mdl2_font(13))
            self._more.setText(icons.glyph("\ue712", ""))  # MDL2: More(⋯)
        else:
            self._more.setText("⋯")
        self._more.setToolTip("更多操作")
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.clicked.connect(self._open_menu)
        lay.addWidget(self._more, 0, Qt.AlignmentFlag.AlignVCenter)

        self._hover_watch = _RowHover(self)

    # ---- hover 支持 ----
    def _hover_targets(self) -> list[QWidget]:
        targets = [self, self._check, self._cell, self._cell.label,
                   self._cell.editor, self._more]
        if self._handle is not None:
            targets.insert(5, self._handle)
        return targets

    def mousePressEvent(self, e) -> None:   # noqa: N802
        # 行内空白也吞掉左键按下：避免事件冒泡到主窗口被当成“拖动窗口”，
        # 保证任务行的拖动/编辑只由行内控件自己处理。
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()
            return
        super().mousePressEvent(e)

    def _cursor_inside(self) -> bool:
        w = QApplication.widgetAt(QCursor.pos())
        while w is not None:
            if w is self:
                return True
            w = w.parentWidget()
        return False

    def _set_hover(self, on: bool) -> None:
        if on != self._hovered:
            self._hovered = on
            self.update()

    # ---- 数据访问 ----
    @property
    def task_id(self) -> int:
        return self._task_id

    def text_value(self) -> str:
        return self._text

    @property
    def done(self) -> bool:
        return self._done

    def paintEvent(self, e) -> None:      # noqa: N802
        if self._hovered:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
            p.fillPath(path, QColor(color("hover")))
            p.end()
        super().paintEvent(e)

    # ---- 信号处理 ----
    def _on_check_toggled(self, checked: bool) -> None:
        self._done = checked
        self._cell.label.set_done(checked)
        self.toggled.emit(self._task_id, checked)

    def start_edit(self) -> None:
        if not self._cell.is_editing():
            self._cell.begin_edit(self._text)

    def finish_editing(self) -> None:
        """窗口隐藏/退出前调用：把正在编辑的内容落库或取消，防止内容丢失。"""
        if self._cell.is_editing():
            t = self._cell.editor.text().strip()
            if t:
                self._cell.editor.commitText.emit(t)
            else:
                self._cell.editor._skip_focus = True
                self._cell.editor.cancelEdit.emit()

    def _on_commit(self, new_text: str) -> None:
        self._cell.end_edit()
        new_text = new_text.strip()
        if new_text and new_text != self._text:
            self._text = new_text
            self._cell.label.setText(new_text)
            self.editSaved.emit(self._task_id, new_text)

    def _on_cancel(self) -> None:
        self._cell.end_edit()

    def _open_menu(self) -> None:
        menu = QMenu(self)
        act_edit = menu.addAction("编辑")
        act_del = menu.addAction("删除")
        menu.exec(self._more.mapToGlobal(self._more.rect().bottomLeft()))
        if menu.activeAction() is act_edit:
            self.start_edit()
        elif menu.activeAction() is act_del:
            self.deleteRequested.emit(self._task_id)
