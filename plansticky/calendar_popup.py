"""日期选择弹层：点击日期标题弹出，单击某天即选中并关闭。

用 Qt.Popup 窗口标志，点击窗口外部自动关闭（和下拉日历一致的手感）。
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QCalendarWidget, QDialog, QVBoxLayout, QWidget


class CalendarPopup(QDialog):
    datePicked = Signal(QDate)

    def __init__(self, parent: QWidget | None, date: QDate):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)

        cal = QCalendarWidget(self)
        cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        cal.setGridVisible(False)
        cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        cal.setSelectedDate(date)
        cal.setCurrentPage(date.year(), date.month())
        cal.clicked.connect(self._on_pick)
        cal.activated.connect(self._on_pick)
        lay.addWidget(cal)
        self._cal = cal
        self.setFixedWidth(cal.sizeHint().width() + 12)

    def _on_pick(self, date: QDate) -> None:
        if not self.isVisible():
            return  # 双击/重复信号防护
        self.datePicked.emit(date)
        self.close()

    def show_below(self, anchor: QWidget) -> None:
        """显示在 anchor 下方；放不下则翻到上方，且不越出屏幕。"""
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        screen = QGuiApplication.screenAt(anchor.mapToGlobal(QPoint(anchor.width() // 2, 0)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        sg = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = min(max(g.x(), sg.left() + 4), sg.right() - w - 4)
        y = g.y()
        if y + h > sg.bottom() - 4:
            y = max(anchor.mapToGlobal(QPoint(0, 0)).y() - h - 6, sg.top() + 4)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
