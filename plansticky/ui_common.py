"""通用小控件：全自定义绘制的圆角复选框、开关、拖拽把手、单行省略文字标签、分段控件。

全部控件在 paintEvent 里读 plansticky.theme.CURRENT，主题切换自动换色，
不依赖 QSS 图片资源。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPolygonF, QPainterPath
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame,
                               QHBoxLayout, QLabel, QLayout, QPushButton,
                               QSizePolicy, QWidget)

from plansticky.theme import color


# ================================================================ 复选框
class CheckBox(QAbstractButton):
    """圆角方块勾选框：未完成=描边，完成=主题色填充+白勾。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("标记完成 / 取消完成")

    def enterEvent(self, e) -> None:      # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:      # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked():
            path = QPainterPath()
            path.addRoundedRect(r, 6, 6)
            p.fillPath(path, QColor(color("accent")))
            pen = QPen(QColor("#FFFFFF"))
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            pts = QPolygonF([
                QPointF(r.x() + r.width() * 0.24, r.y() + r.height() * 0.52),
                QPointF(r.x() + r.width() * 0.42, r.y() + r.height() * 0.70),
                QPointF(r.x() + r.width() * 0.76, r.y() + r.height() * 0.30),
            ])
            p.drawPolyline(pts)
        else:
            pen = QPen(QColor(color("checkBorder")))
            pen.setWidthF(1.6)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 6, 6)
            if self.underMouse():
                p.setBrush(QColor(color("hover")))
                p.drawRoundedRect(r, 6, 6)
        p.end()


# ================================================================ 开关
class SwitchButton(QAbstractButton):
    """小开关（如“隐藏已完成”）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(34, 20)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e) -> None:      # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:      # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        track = QColor(color("accent")) if self.isChecked() else QColor(color("checkBorder"))
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, track)
        # 滑块
        d = r.height() - 4
        x = r.width() - d - 2 if self.isChecked() else 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color("bg")))
        p.drawEllipse(QRectF(x, r.y() + 2, d, d))
        p.end()


# ================================================================ 拖拽把手
class DragHandle(QWidget):
    """行内拖动排序把手：按住并移动超过阈值触发 dragRequested。"""

    dragRequested = Signal(QPoint)  # 参数=按下时的全局坐标（供列表生成拖影定位）

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(18, 24)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动排序")
        self._press_global: QPoint | None = None
        self._dragging = False

    def paintEvent(self, e) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(color("text" if self.underMouse() else "faint"))
        col.setAlpha(180 if self.underMouse() else 110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        cx = self.width() / 2
        cy = self.height() / 2
        for dx in (-3, 3):
            p.drawRoundedRect(QRectF(cx + dx - 1.5, cy - 5, 3, 10), 1.5, 1.5)
        p.end()

    def enterEvent(self, e) -> None:      # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:      # noqa: N802
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._dragging = False
            e.accept()   # 吞掉按下事件，避免冒泡到主窗口触发“拖动窗口”
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:   # noqa: N802
        if self._press_global is not None and not self._dragging:
            from PySide6.QtWidgets import QApplication
            dist = (e.globalPosition().toPoint() - self._press_global).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._dragging = True
                pos = self._press_global
                self._press_global = None
                self.dragRequested.emit(pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_global = None
            self._dragging = False
            e.accept()
            return
        super().mouseReleaseEvent(e)


# ================================================================ 文字标签
class TextLabel(QWidget):
    """单行省略标签：超宽自动缩略 + 悬停提示全文；支持“完成”删除线样式。

    - 单击（无位移松手）发出 clicked，用于“点击任务文字进入编辑”；
    - 按住拖动超过阈值发出 dragStart(全局按下点)，让整行文字也能拖动排序。

    按下事件必须 accept()：否则会冒泡到主窗口，被当成“拖动窗口”。"""
    clicked = Signal()
    dragStart = Signal(QPoint)   # 参数 = 按下时的全局坐标

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._done = False
        self._press_global: QPoint | None = None
        self._dragged = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(20)

    # ---- 属性 ----
    def text(self) -> str:
        return self._text

    def setText(self, t: str) -> None:    # noqa: N802
        if t != self._text:
            self._text = t
            self.setToolTip("")
            self.update()
            self.updateGeometry()

    def is_done(self) -> bool:
        return self._done

    def set_done(self, done: bool) -> None:
        if done != self._done:
            self._done = done
            self.update()

    # ---- 尺寸 ----
    def heightHint(self) -> int:          # noqa: N802
        return QFontMetrics(self.font()).height() + 4

    def sizeHint(self) -> "QSize":        # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self._text) + 4,
                     self.heightHint())

    # ---- 事件 ----
    def mousePressEvent(self, e) -> None:   # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._dragged = False
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:    # noqa: N802
        if self._press_global is not None and not self._dragged:
            from PySide6.QtWidgets import QApplication
            moved = (e.globalPosition().toPoint() - self._press_global).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._dragged = True
                pos = self._press_global
                self._press_global = None
                self.dragStart.emit(pos)
                e.accept()
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            if not self._dragged:
                self.clicked.emit()
            self._press_global = None
            self._dragged = False
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, e) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        fm = QFontMetrics(self.font())
        full = self._text
        elided = fm.elidedText(full, Qt.TextElideMode.ElideRight, self.width() - 2)
        # 缩略时提示全文
        tip = full if elided != full else ""
        if self.toolTip() != tip:
            self.setToolTip(tip)
        font = self.font()
        pen_color = QColor(color("doneText" if self._done else "text"))
        if self._done:
            font.setStrikeOut(True)
            pen_color.setAlpha(200)
        p.setFont(font)
        p.setPen(pen_color)
        p.drawText(self.rect().adjusted(0, 0, -2, 0),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        p.end()


# ================================================================ 分段控件
class Segmented(QFrame):
    """分段控件（长期计划 / 短期计划 / 记账 / 日记 / 签到）。

    compact=True 时收紧内边距与字号，用于 5 个 Tab 挤在窄窗口里的情况。
    """

    indexChanged = Signal(int)

    def __init__(self, items: list[str], parent: QWidget | None = None,
                 compact: bool = False):
        super().__init__(parent)
        self.setObjectName("segTrack")
        if compact:
            self.setProperty("compact", "true")
        lay = QHBoxLayout(self)
        margin = 2 if compact else 3
        lay.setContentsMargins(margin, margin, margin, margin)
        lay.setSpacing(1 if compact else 2)
        self._buttons = []
        # 互斥组：保证同一时刻只有一个 Tab 处于激活态；
        # 点击已激活的 Tab 不会把它关掉（否则视觉与页面会错位）。
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for i, item in enumerate(items):
            b = QPushButton(item, self)
            b.setProperty("cls", "seg")
            if compact:
                b.setProperty("compact", "true")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.indexChanged.emit(idx))
            lay.addWidget(b)
            self._group.addButton(b, i)
            self._buttons.append(b)
        if self._buttons:
            self._buttons[0].setChecked(True)

    def current_index(self) -> int:
        for i, b in enumerate(self._buttons):
            if b.isChecked():
                return i
        return 0

    def set_current_index(self, idx: int) -> None:
        """程序化切换：与点击一致，真正发出 indexChanged（setChecked 本身不发）。"""
        if not 0 <= idx < len(self._buttons):
            return
        if self.current_index() == idx:
            return
        self._buttons[idx].setChecked(True)
        self.indexChanged.emit(idx)

    def button(self, idx: int) -> QPushButton | None:
        if 0 <= idx < len(self._buttons):
            return self._buttons[idx]
        return None


# ================================================================ 提示条
class Toast(QLabel):
    """窗口内浮动提示条（替代旧版“失败静默”）。

    用法：host.toast("已记下一笔")；错误用 host.toast("...", level="error")，
    错误停留时间更长，便于看清。
    """

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self.setObjectName("toast")
        self.setProperty("level", "ok")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, level: str = "ok",
                     duration: int | None = None) -> None:
        self.setProperty("level", level if level in ("ok", "error") else "ok")
        self.setProperty("info", "true" if level not in ("ok", "error") else "false")
        self.setText(str(text))
        # 改了动态属性要重新 polish 才会应用新 QSS
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        if duration is None:
            duration = 4200 if level == "error" else 2200
        # 错误信息按文字长度延长，避免长句没看完就消失
        if level == "error":
            duration = max(duration, min(9000, 1600 + 90 * len(str(text))))
        self._timer.start(duration)

    def _reposition(self) -> None:
        host = self._host
        margin = 14
        width = min(self.sizeHint().width() + 8, max(160, host.width() - margin * 2))
        self.setFixedWidth(width)
        self.adjustSize()
        x = (host.width() - self.width()) // 2
        y = host.height() - self.height() - margin
        self.move(max(margin, x), max(margin, y))

    def resizeEvent(self, e) -> None:      # noqa: N802
        super().resizeEvent(e)
        if self.isVisible():
            self._reposition()


# ================================================================ 流式布局
class FlowLayout(QLayout):
    """自动换行的横向布局（标签胶囊、分类格子用）。

    Qt 没有内置的流式布局，按官方示例实现 heightForWidth 版本。
    """

    def __init__(self, parent: QWidget | None = None, margin: int = 0,
                 spacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item) -> None:          # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):             # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):             # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):            # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:      # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:   # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:    # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:              # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:           # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(),
                                  -margins.right(), -margins.bottom())
        x, y = effective.x(), effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()
