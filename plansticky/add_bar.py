"""底部“添加计划”输入条。

折叠态 = 一条虚线“添加计划”按钮；
展开态 = 输入框：
    Enter        保存并停留（方便连续录入多条）
    Esc          取消并收起
    失焦         非空自动保存并收起（防止手滑点走丢内容）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget


class _CommitLineEdit(QLineEdit):
    commitText = Signal(str)    # 失焦时非空内容
    cancelEdit = Signal()       # Esc

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._skip_focus = False

    def keyPressEvent(self, e) -> None:   # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            e.accept()
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


class AddBar(QWidget):
    """单行添加条：按钮 <-> 输入框 两种形态。"""

    addRequested = Signal(str)

    def __init__(self, placeholder: str = "输入计划内容，Enter 保存 · Esc 取消",
                 parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._btn = QPushButton("+ 添加计划", self)
        self._btn.setObjectName("addBtn")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFixedHeight(34)
        self._btn.clicked.connect(self._open_editor)
        lay.addWidget(self._btn)

        self._edit = _CommitLineEdit(self)
        self._placeholder = placeholder
        self._edit.setPlaceholderText(placeholder)
        self._edit.setFixedHeight(34)
        self._edit.returnPressed.connect(self._on_enter)
        self._edit.cancelEdit.connect(self._collapse)
        self._edit.commitText.connect(self._on_blur_commit)
        self._edit.hide()
        lay.addWidget(self._edit)

    def set_placeholder(self, text: str) -> None:
        """切换日期后更新占位提示（如 输入9月8日计划…）。"""
        if text != self._placeholder:
            self._placeholder = text
            self._edit.setPlaceholderText(text)

    # ---- 状态 ----
    def is_editing(self) -> bool:
        return self._edit.isVisible()

    def flush(self) -> None:
        """日期切换等场景：把正在输入的内容先提交（保持打开则继续可输入）。"""
        if self.is_editing() and self._edit.text().strip():
            t = self._edit.text().strip()
            self._edit.clear()
            self.addRequested.emit(t)

    # ---- 内部 ----
    def _open_editor(self) -> None:
        self._btn.hide()
        self._edit._skip_focus = False
        self._edit.show()
        self._edit.setFocus(Qt.FocusReason.MouseFocusReason)

    def _collapse(self) -> None:
        if self._edit.isVisible():
            self._edit.hide()
        self._btn.show()

    def _on_enter(self) -> None:
        t = self._edit.text().strip()
        self._edit.clear()
        if t:
            self.addRequested.emit(t)
        # 保持展开，方便连续输入

    def _on_blur_commit(self, t: str) -> None:
        self._collapse()
        self.addRequested.emit(t)
