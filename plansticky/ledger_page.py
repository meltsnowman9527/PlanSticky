"""记账页：记一笔 / 月度概览 / 账目明细 / 图表 / CSV 与备份。

口径与文案对齐旧版「清楚账本」（见 docs/融合规格.md），但顺手修掉了旧版缺陷：

- **笔数只算支出**（旧版把收入也算进「N 笔」，与金额口径不一致）；
- **CSV 类型往返**：导出写「收入/支出」，导入同时兼容「收入」与 ``income``，
  不再出现「收入导入后变支出」；
- **失败不再静默**：所有校验失败都通过提示条明确告知原因。

数据由 `ledger_db.LedgerDatabase` 提供（与 plans.db 分开的 ledger.db）。
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import date as _date

from PySide6.QtCore import (QAbstractTableModel, QDate, QModelIndex, QPoint,
                            QRectF, QSize, Qt, Signal)
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QPainter,
                           QPainterPath, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit,
                               QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QTableView, QVBoxLayout, QWidget)

from plansticky import config
from plansticky.ledger_charts import DonutChart, TrendChart
from plansticky.ledger_db import (CATEGORIES, CATEGORY_FALLBACK, CATEGORY_NAMES,
                                  TYPE_EXPENSE, TYPE_INCOME, LedgerDatabase,
                                  Transaction, ValidationError, category_color,
                                  category_icon)
from plansticky.theme import color
from plansticky.ui_common import Segmented

TREND_RANGES = [(7, "7天"), (30, "30天"), (90, "三个月")]
FMT = "%Y-%m-%d"
# 输入控件（下拉框/日期框/输入框）的统一最小高度。
# Qt 默认 sizeHint 只有 22px，而 QSS 的 padding 会再压缩内容区，
# 导致文字被上下裁掉（实测"看起来显示有问题"就是这个）。
CONTROL_HEIGHT = 26


def _shorten_path(path: str) -> str:
    """把数据目录路径缩成适合一行显示的形式（完整路径放 tooltip）。

    `QLabel` 的最小宽度由文本宽度决定，长路径会把整个记账页的最小宽度
    撑到 600px 以上，从而挤扁明细与图表视图，所以这里必须缩短。
    """
    text = str(path or "")
    appdata = os.environ.get("APPDATA", "")
    if appdata and text.lower().startswith(appdata.lower()):
        return "%APPDATA%" + text[len(appdata):]
    home = os.path.expanduser("~")
    if home and text.lower().startswith(home.lower()):
        return "~" + text[len(home):]
    # 兜底：只留最后两级（例如 …\\PlanSticky\\ledger.db）
    parts = text.replace("\\", "/").rstrip("/").split("/")
    if len(parts) > 2:
        return "…/" + "/".join(parts[-2:])
    return text


def money(value: float, sign: bool = False, tx_type: str = TYPE_EXPENSE) -> str:
    """货币格式：¥1,234.00；sign=True 时按类型加 +/- 号。"""
    text = f"¥{abs(float(value)):,.2f}"
    if not sign:
        return text
    return ("+" if tx_type == TYPE_INCOME else "-") + text


def today_key() -> str:
    return _date.today().strftime(FMT)


def _category_badge(icon: str, hex_color: str, size: int = 18) -> QIcon:
    """分类单字图标：分类色的圆角小方块 + 白字。"""
    from PySide6.QtGui import QIcon, QPainter, QPainterPath
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    painter.fillPath(path, QColor(hex_color))
    font = painter.font()
    font.setPixelSize(int(size * 0.62))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, icon)
    painter.end()
    return QIcon(pixmap)


def current_month() -> str:
    return _date.today().strftime("%Y-%m")


class MonthPicker(QWidget):
    """月份选择器：‹ 2026年9月 › + 可选「本月」按钮。"""

    monthChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._month = current_month()

        self._btn_prev = QPushButton("‹", self)
        self._btn_prev.setProperty("cls", "nav")
        self._btn_prev.setFixedSize(22, 24)
        self._btn_prev.setToolTip("上个月")
        self._btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_prev.clicked.connect(lambda: self.shift(-1))
        lay.addWidget(self._btn_prev)

        self._label = QPushButton(self)
        self._label.setProperty("cls", "date")
        self._label.setFixedHeight(24)
        self._label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._label.setToolTip("点击回到本月")
        self._label.clicked.connect(lambda: self.set_month(current_month()))
        lay.addWidget(self._label)

        self._btn_next = QPushButton("›", self)
        self._btn_next.setProperty("cls", "nav")
        self._btn_next.setFixedSize(22, 24)
        self._btn_next.setToolTip("下个月")
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_next.clicked.connect(lambda: self.shift(1))
        lay.addWidget(self._btn_next)

        self._render()

    def month(self) -> str:
        return self._month

    def set_month(self, month: str) -> None:
        try:
            year, mon = int(str(month)[:4]), int(str(month)[5:7])
        except (ValueError, IndexError):
            return
        if not (1 <= mon <= 12) or not (1900 <= year <= 2200):
            return
        new = f"{year:04d}-{mon:02d}"
        if new == self._month:
            self._render()
            return
        self._month = new
        self._render()
        self.monthChanged.emit(self._month)

    def shift(self, delta: int) -> None:
        year, mon = int(self._month[:4]), int(self._month[5:7])
        total = year * 12 + (mon - 1) + delta
        self.set_month(f"{total // 12:04d}-{total % 12 + 1:02d}")

    def _render(self) -> None:
        year, mon = int(self._month[:4]), int(self._month[5:7])
        self._label.setText(f"{year}年{mon}月")
        self._btn_next.setEnabled(self._month < current_month())


# ============================================================== 账目表格模型
HEADERS = ["日期", "分类", "用途", "备注", "金额"]


class TransactionModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[Transaction] = []

    def set_rows(self, rows: list[Transaction]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, index: int) -> Transaction | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    @property
    def rows(self) -> list[Transaction]:
        return self._rows

    # ---- Qt 接口 ----
    def rowCount(self, parent=QModelIndex()) -> int:      # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:   # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return HEADERS[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        tx = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return tx.date
            if col == 1:
                return tx.category
            if col == 2:
                return tx.purpose
            if col == 3:
                return tx.note or "—"
            if col == 4:
                return money(tx.amount, sign=True, tx_type=tx.type)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 4:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        elif role == Qt.ItemDataRole.ForegroundRole and col == 4:
            return QColor(color("income" if tx.is_income else "expense"))
        elif role == Qt.ItemDataRole.FontRole:
            if col == 4:
                font = QFont()
                font.setBold(True)
                return font
            if col == 2:
                font = QFont()
                font.setBold(True)
                return font
        elif role == Qt.ItemDataRole.ToolTipRole:
            parts = [f"{tx.date} · {tx.category}", tx.purpose]
            if tx.note:
                parts.append(f"备注：{tx.note}")
            parts.append(money(tx.amount, sign=True, tx_type=tx.type))
            return "\n".join(parts)
        return None


# ================================================================== 记账表单
class CategoryGrid(QWidget):
    """分类选择网格：每格 = 彩色单字图标 + 名称。

    图标用分类自身颜色画成圆角小方块，比纯文字按钮好认；
    选中态用「同色淡底 + 同色描边」，不用整块实心（太重）。
    """

    changed = Signal(str)

    COLS = 4

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._selected = CATEGORY_NAMES[0]
        self._buttons: dict[str, QPushButton] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(5)
        for i, (name, icon, hex_color) in enumerate(CATEGORIES):
            btn = QPushButton(f" {name}", self)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setStyleSheet(
                f"QPushButton {{ border:1px solid {color('line')};"
                f" border-radius:8px; padding:5px 6px; font-size:12px;"
                f" color:{color('sub')}; text-align:left; }}"
                f"QPushButton:hover {{ background:{color('hover')}; }}"
                f"QPushButton:checked {{ border:1px solid {hex_color};"
                f" color:{hex_color}; font-weight:600;"
                f" background:{hex_color}1F; }}")
            btn.setIcon(_category_badge(icon, hex_color))
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(lambda _=False, n=name: self.set_selected(n))
            grid.addWidget(btn, i // self.COLS, i % self.COLS)
            self._buttons[name] = btn
        self._sync()

    def selected(self) -> str:
        return self._selected

    def set_selected(self, name: str) -> None:
        if name not in self._buttons:
            name = CATEGORY_FALLBACK
        if name == self._selected and self._buttons[name].isChecked():
            return
        self._selected = name
        self._sync()
        self.changed.emit(name)

    def _sync(self) -> None:
        for name, btn in self._buttons.items():
            btn.setChecked(name == self._selected)

class TransactionDialog(QDialog):
    """记一笔 / 修改这笔账。

    视觉要点（见 docs/记账界面设计规范.md）：
    - 金额是主角：右侧大号输入，打开即聚焦；
    - 日期与金额并排，省一行高度；
    - 类型用紧凑双段，选中为淡底描边，不用整块实心；
    - 分类走 CategoryGrid（彩色图标 + 名称）；
    - 底部 取消(ghost) + 保存(primary) 右对齐，错误提示在按钮上方。
    """

    def __init__(self, db: LedgerDatabase, parent: QWidget | None = None,
                 tx: Transaction | None = None):
        super().__init__(parent)
        self._db = db
        self._editing = tx
        self._type = tx.type if tx else TYPE_EXPENSE
        self.setWindowTitle("修改这笔账" if tx else "记一笔")
        self.setModal(True)
        # 主窗口只有 400px 宽，表单不能比它更宽（否则挤在屏幕上很难看）
        self.setMinimumWidth(340)
        self.setMaximumWidth(392)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        # ---- 标题区
        title = QLabel("修改这笔账" if tx else "记一笔", self)
        title.setObjectName("sectionTitle")
        title_font = title.font()
        title_font.setPointSizeF(title_font.pointSizeF() + 3)
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)
        hint = QLabel("数据只保存在这台电脑中", self)
        hint.setObjectName("cardHint")
        root.addWidget(hint)

        # ---- 金额（主角）：日期（窄）+ 金额（宽）并排
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(8)

        date_box = QVBoxLayout()
        date_box.setSpacing(3)
        date_label = QLabel("日期", self)
        date_label.setObjectName("fieldLabel")
        date_box.addWidget(date_label)
        self._date = QDateEdit(self)
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._date.setMinimumHeight(CONTROL_HEIGHT)
        self._date.setDate(QDate.fromString(tx.date, "yyyy-MM-dd") if tx
                           else QDate.currentDate())
        date_box.addWidget(self._date)
        row.addLayout(date_box, 0)

        amount_box = QVBoxLayout()
        amount_box.setSpacing(3)
        amount_label = QLabel("金额（元）", self)
        amount_label.setObjectName("fieldLabel")
        amount_box.addWidget(amount_label)
        self._amount = QDoubleSpinBox(self)
        self._amount.setDecimals(2)
        # 允许 0：真实最小值由 validate 判定（必须 > 0）。
        # 若把 minimum 设成 0.01，表单一打开就已经是 0.01，
        # 用户只填用途就保存会静默记成 0.01 元（旧版是空的必填框）。
        self._amount.setRange(0.0, 9_999_999.99)
        self._amount.setSingleStep(1.0)
        self._amount.setValue(tx.amount if tx else 0.0)
        if tx is None:
            self._amount.setSpecialValueText("0.00")
        self._amount.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._amount.setMinimumHeight(CONTROL_HEIGHT)
        amount_font = self._amount.font()
        amount_font.setPointSizeF(amount_font.pointSizeF() + 2)
        self._amount.setFont(amount_font)
        amount_box.addWidget(self._amount)
        row.addLayout(amount_box, 1)
        root.addLayout(row)

        # ---- 类型：紧凑双段
        type_row = QHBoxLayout()
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(8)
        type_label = QLabel("类型", self)
        type_label.setObjectName("fieldLabel")
        type_label.setFixedWidth(52)
        type_row.addWidget(type_label)
        self._btn_expense = QPushButton("支出", self)
        self._btn_income = QPushButton("收入", self)
        for btn in (self._btn_expense, self._btn_income):
            btn.setCheckable(True)
            btn.setMinimumHeight(CONTROL_HEIGHT)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedWidth(76)
        self._btn_expense.clicked.connect(lambda: self._set_type(TYPE_EXPENSE))
        self._btn_income.clicked.connect(lambda: self._set_type(TYPE_INCOME))
        type_row.addWidget(self._btn_expense)
        type_row.addWidget(self._btn_income)
        type_row.addStretch(1)
        root.addLayout(type_row)

        # ---- 用途 / 备注
        for attr, label_text, placeholder, maxlen in (
                ("_purpose", "用途", "例如：午餐、地铁月卡", 40),
                ("_note", "备注", "可选", 100)):
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            lab = QLabel(label_text, self)
            lab.setObjectName("fieldLabel")
            lab.setFixedWidth(52)
            line.addWidget(lab)
            edit = QLineEdit(self)
            edit.setMaxLength(maxlen)
            edit.setPlaceholderText(placeholder)
            edit.setMinimumHeight(CONTROL_HEIGHT)
            edit.setText((tx.purpose if attr == "_purpose" else tx.note) if tx else "")
            line.addWidget(edit, 1)
            root.addLayout(line)
            setattr(self, attr, edit)

        # ---- 分类
        cat_label = QLabel("分类", self)
        cat_label.setObjectName("fieldLabel")
        root.addWidget(cat_label)
        self._categories = CategoryGrid(self)
        if tx:
            self._categories.set_selected(tx.category)
        root.addWidget(self._categories)

        # ---- 错误提示（按钮上方）
        self._error = QLabel("", self)
        self._error.setObjectName("invalidHint")
        self._error.setWordWrap(True)
        self._error.hide()
        root.addWidget(self._error)

        # ---- 底部按钮：取消(ghost) + 保存(primary)，右对齐
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 2, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self._btn_cancel = QPushButton("取消", self)
        self._btn_cancel.setProperty("cls", "nav")
        self._btn_cancel.setMinimumHeight(30)
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self._btn_cancel)
        self._btn_save = QPushButton("保存账目", self)
        self._btn_save.setProperty("btn", "primary")
        self._btn_save.setMinimumHeight(30)
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.setDefault(True)
        self._btn_save.clicked.connect(self._on_save)
        buttons.addWidget(self._btn_save)
        root.addLayout(buttons)

        self._set_type(self._type)
        self._amount.setFocus()
        if self._amount.value() > 0:
            self._amount.selectAll()

    def _set_type(self, tx_type: str) -> None:
        """类型按钮：选中 = 该类型的柔和淡底 + 同色描边 + 同色粗体字。

        底色用预置的柔和色（incomeSoft/expenseSoft），不要用「主色 + 低透明度」
        叠加 —— 半透明主色压在灰底上会发浑（支出会变成脏棕色）。
        """
        self._type = tx_type
        for btn, kind in ((self._btn_expense, TYPE_EXPENSE),
                          (self._btn_income, TYPE_INCOME)):
            accent = color("expense" if kind == TYPE_EXPENSE else "income")
            soft = color("expenseSoft" if kind == TYPE_EXPENSE else "incomeSoft")
            if kind == tx_type:
                btn.setStyleSheet(
                    f"QPushButton {{ border:1px solid {accent}; border-radius:7px;"
                    f" background:{soft}; color:{accent}; font-weight:600;"
                    f" font-size:12px; }}")
                btn.setChecked(True)
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ border:1px solid {color('line')};"
                    f" border-radius:7px; background:transparent;"
                    f" color:{color('sub')}; font-size:12px; }}"
                    f"QPushButton:hover {{ background:{color('hover')}; }}")
                btn.setChecked(False)

    def _on_save(self) -> None:
        day = self._date.date().toString("yyyy-MM-dd")
        try:
            if self._editing:
                self._db.update_transaction(
                    self._editing.id, day, self._amount.value(), self._type,
                    self._categories.selected(), self._purpose.text(),
                    self._note.text())
            else:
                self._db.add_transaction(
                    day, self._amount.value(), self._type,
                    self._categories.selected(), self._purpose.text(),
                    self._note.text())
        except ValidationError as exc:
            # 修旧版缺陷：失败必须有明确提示，不能静默
            self._error.setText(str(exc))
            self._error.show()
            return
        self.accept()

class LedgerPage(QWidget):
    """记账页：明细 / 图表 / 工具 三个二级视图。"""

    TABS = ["明细", "图表", "工具"]
    IMPORTED = Signal(int)      # 导入/恢复成功，携带条数（供主窗口提示）

    def __init__(self, db, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_path = ""
        self._open_error = ""
        self._db = self._open_ledger()
        self._month = current_month()
        self._build_ui()
        self.refresh()

    def _open_ledger(self) -> LedgerDatabase:
        """打开账本库；损坏时降级到内存库，保证整个程序仍可使用。

        计划便签的核心功能不能因为账本库坏了就打不开 —— 旧版是单进程单库，
        这里两个库，必须隔离故障。
        """
        try:
            db = LedgerDatabase()
            self._db_path = db.path
            return db
        except Exception as exc:                       # noqa: BLE001
            self._open_error = f"账本数据库打开失败，已临时降级到内存库：{exc}"
            fallback = LedgerDatabase(":memory:")
            self._db_path = fallback.path
            return fallback

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        head = QWidget(self)
        head.setFixedHeight(28)
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 2, 0)
        hh.setSpacing(4)
        self._picker = MonthPicker(head)
        self._picker.monthChanged.connect(self._on_month_changed)
        hh.addWidget(self._picker)
        hh.addStretch(1)
        self._seg = Segmented(self.TABS, head, compact=True)
        self._seg.indexChanged.connect(self._on_subtab)
        hh.addWidget(self._seg)
        root.addWidget(head)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_detail_page())
        self._stack.addWidget(self._build_charts_page())
        self._stack.addWidget(self._build_tools_page())
        root.addWidget(self._stack, 1)

    # ---- 明细页 ----
    def _build_detail_page(self) -> QWidget:
        page = QScrollArea(self)
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget(page)
        page.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(6)

        v.addWidget(self._build_summary_card(inner))
        v.addWidget(self._build_filter_bar(inner))

        # 表格标题行：左边"账目明细 · N 笔"，右边操作按钮（不再把按钮丢在左下角）
        list_head = QWidget(inner)
        lh = QHBoxLayout(list_head)
        lh.setContentsMargins(2, 0, 2, 0)
        lh.setSpacing(6)
        self._list_title = QLabel("账目明细", list_head)
        self._list_title.setObjectName("tableTitle")
        lh.addWidget(self._list_title)
        self._result_label = QLabel("0 笔", list_head)
        self._result_label.setObjectName("cardHint")
        lh.addWidget(self._result_label)
        lh.addStretch(1)
        self._btn_edit = QPushButton("编辑", list_head)
        self._btn_edit.setProperty("btn", "outline")
        self._btn_edit.setMinimumHeight(24)
        self._btn_edit.setToolTip("编辑选中的账目（也可双击表格行）")
        self._btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_edit.clicked.connect(self._edit_selected)
        lh.addWidget(self._btn_edit)
        self._btn_delete = QPushButton("删除", list_head)
        self._btn_delete.setProperty("btn", "outline")
        self._btn_delete.setProperty("tone", "danger")
        self._btn_delete.setMinimumHeight(24)
        self._btn_delete.setToolTip("删除选中的账目（右键表格行也可）")
        self._btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_delete.clicked.connect(self._delete_selected)
        lh.addWidget(self._btn_delete)
        v.addWidget(list_head)

        self._model = TransactionModel(self)
        self._table = QTableView(inner)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_menu)
        self._table.doubleClicked.connect(lambda _i: self._edit_selected())
        header = self._table.horizontalHeader()
        # 日期/分类/金额按内容自适应；用途拉伸吃剩余宽度；备注给固定 76px
        # （备注多半是空的，给太宽会挤掉用途）
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(3, 76)
        header.setHighlightSections(False)
        self._table.setColumnWidth(3, 76)
        self._table.setMinimumHeight(150)
        self._table.setMinimumWidth(0)
        # 窄窗口下表格内部不横向滚动，列自适应压缩
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setWordWrap(False)
        v.addWidget(self._table, 1)

        self._empty = QLabel("这里还空着\n记下第一笔支出，图表会自动生成", inner)
        self._empty.setObjectName("emptyHint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setMinimumHeight(90)
        self._empty.hide()
        v.addWidget(self._empty)

        self._btn_add = QPushButton("＋ 记一笔", inner)
        self._btn_add.setProperty("btn", "primary")
        self._btn_add.setMinimumHeight(32)
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.clicked.connect(self.add_transaction)
        v.addWidget(self._btn_add)
        return page

    def _build_summary_card(self, parent: QWidget) -> QWidget:
        """月度概览卡：结构上只分三组，避免"四块互不相关的东西"。

        - 组 1：本月支出 + 笔数（一行）
        - 组 2：大号金额 + 预算进度（金额在左、预算在右对齐同一基线）
        - 组 3：日均 / 收入 / 最高单笔（一行三段，用分隔点）
        """
        card = QFrame(parent)
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(7)

        # ---- 组 1
        row1 = QWidget(card)
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(6)
        title = QLabel("本月支出", row1)
        title.setObjectName("cardTitle")
        r1.addWidget(title)
        r1.addStretch(1)
        self._count_label = QLabel("0 笔", row1)
        self._count_label.setObjectName("cardHint")
        r1.addWidget(self._count_label)
        v.addWidget(row1)

        # ---- 组 2：金额（左）+ 预算与百分比（右）
        row2 = QWidget(card)
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(8)
        self._total_label = QLabel("¥0.00", row2)
        self._total_label.setObjectName("moneyBig")
        r2.addWidget(self._total_label)
        r2.addStretch(1)

        budget_box = QVBoxLayout()
        budget_box.setSpacing(2)
        budget_head = QHBoxLayout()
        budget_head.setSpacing(4)
        budget_label = QLabel("月预算", row2)
        budget_label.setObjectName("cardHint")
        budget_head.addWidget(budget_label)
        self._budget_edit = QDoubleSpinBox(row2)
        self._budget_edit.setDecimals(0)
        self._budget_edit.setRange(1, 9_999_999)
        self._budget_edit.setFixedWidth(72)
        self._budget_edit.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self._budget_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._budget_edit.setToolTip("月预算，改动后立即保存")
        self._budget_edit.setMinimumWidth(0)
        self._budget_edit.editingFinished.connect(self._on_budget_changed)
        budget_head.addWidget(self._budget_edit)
        budget_box.addLayout(budget_head)
        self._budget_rate = QLabel("0%", row2)
        self._budget_rate.setObjectName("cardHint")
        self._budget_rate.setAlignment(Qt.AlignmentFlag.AlignRight)
        budget_box.addWidget(self._budget_rate)
        r2.addLayout(budget_box)
        v.addWidget(row2)

        self._budget_bar = QProgressBar(card)
        self._budget_bar.setTextVisible(False)
        self._budget_bar.setFixedHeight(6)
        self._budget_bar.setRange(0, 100)
        v.addWidget(self._budget_bar)

        # ---- 组 3：辅助指标（一行三段）
        self._extra_label = QLabel("", card)
        self._extra_label.setObjectName("cardHint")
        self._extra_label.setWordWrap(True)
        v.addWidget(self._extra_label)
        return card

    def _build_filter_bar(self, parent: QWidget) -> QWidget:
        """筛选栏：两行紧凑布局（窄窗口 400px 下也不出横向滚动条）。

        第 1 行：搜索框（占满）
        第 2 行：分类 / 类型 / 日期范围开关 + 起止日期
        """
        bar = QWidget(parent)
        v = QVBoxLayout(bar)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self._search = QLineEdit(bar)
        self._search.setPlaceholderText("搜索用途或备注")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(0)
        self._search.setMinimumHeight(CONTROL_HEIGHT)
        self._search.textChanged.connect(self.refresh_table)
        v.addWidget(self._search)

        row = QWidget(bar)
        rh = QHBoxLayout(row)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(3)

        self._filter_cat = QComboBox(row)
        self._filter_cat.addItem("全部分类")
        for name in CATEGORY_NAMES:
            self._filter_cat.addItem(f"{category_icon(name)} {name}")
        self._filter_cat.setMinimumWidth(72)
        self._filter_cat.setMinimumHeight(CONTROL_HEIGHT)
        self._filter_cat.currentIndexChanged.connect(self.refresh_table)
        rh.addWidget(self._filter_cat, 1)

        self._filter_type = QComboBox(row)
        self._filter_type.addItem("全部类型", "all")
        self._filter_type.addItem("支出", TYPE_EXPENSE)
        self._filter_type.addItem("收入", TYPE_INCOME)
        self._filter_type.setMinimumWidth(72)
        self._filter_type.setMinimumHeight(CONTROL_HEIGHT)
        self._filter_type.currentIndexChanged.connect(self.refresh_table)
        rh.addWidget(self._filter_type, 1)

        self._range_on = QCheckBox("按日期范围", row)
        self._range_on.setToolTip("勾选后用起止日期筛选，此时忽略月份")
        self._range_on.toggled.connect(self._on_range_toggled)
        rh.addWidget(self._range_on)
        v.addWidget(row)

        date_row = QWidget(bar)
        dr = QHBoxLayout(date_row)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.setSpacing(3)
        self._date_from = QDateEdit(date_row)
        self._date_from.setCalendarPopup(True)
        self._date_from.setDisplayFormat("yyyy-MM-dd")
        self._date_from.setDate(QDate.currentDate().addDays(-30))
        self._date_from.setEnabled(False)
        # 窄窗口下拉日期框不能按 sizeHint（164px）撑宽布局
        self._date_from.setMinimumWidth(96)
        self._date_from.setMinimumHeight(CONTROL_HEIGHT)
        self._date_from.dateChanged.connect(self.refresh_table)
        dr.addWidget(self._date_from, 1)
        tilde = QLabel("~", date_row)
        dr.addWidget(tilde)
        self._date_to = QDateEdit(date_row)
        self._date_to.setCalendarPopup(True)
        self._date_to.setDisplayFormat("yyyy-MM-dd")
        self._date_to.setDate(QDate.currentDate())
        self._date_to.setEnabled(False)
        self._date_to.setMinimumWidth(96)
        self._date_to.setMinimumHeight(CONTROL_HEIGHT)
        self._date_to.dateChanged.connect(self.refresh_table)
        dr.addWidget(self._date_to, 1)
        dr.addStretch(1)
        # 默认藏起日期范围：禁用状态的输入框占一行会让筛选栏显得很乱
        date_row.setVisible(False)
        self._date_row = date_row
        v.addWidget(date_row)
        return bar

    # ---- 图表页 ----
    def _build_charts_page(self) -> QWidget:
        page = QScrollArea(self)
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget(page)
        page.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(6)

        trend_card = QFrame(inner)
        trend_card.setObjectName("cardFlat")
        tv = QVBoxLayout(trend_card)
        tv.setContentsMargins(10, 8, 10, 8)
        tv.setSpacing(4)
        head = QWidget(trend_card)
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(4)
        self._trend_title = QLabel("近 7 天支出", head)
        self._trend_title.setObjectName("cardTitle")
        hh.addWidget(self._trend_title)
        hh.addStretch(1)
        self._trend_seg = Segmented([label for _n, label in TREND_RANGES], head,
                                    compact=True)
        self._trend_seg.indexChanged.connect(self._on_trend_range)
        hh.addWidget(self._trend_seg)
        tv.addWidget(head)
        self._trend = TrendChart(self._db, trend_card)
        tv.addWidget(self._trend, 1)
        v.addWidget(trend_card, 1)

        mix_card = QFrame(inner)
        mix_card.setObjectName("cardFlat")
        mv = QVBoxLayout(mix_card)
        mv.setContentsMargins(10, 8, 10, 8)
        mv.setSpacing(4)
        mix_title = QLabel("分类占比（本月支出）", mix_card)
        mix_title.setObjectName("cardTitle")
        mv.addWidget(mix_title)
        mix_row = QWidget(mix_card)
        mh = QHBoxLayout(mix_row)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.setSpacing(10)
        self._donut = DonutChart(self._db, mix_row)
        mh.addWidget(self._donut, 0, Qt.AlignmentFlag.AlignTop)
        self._legend = QLabel("", mix_row)
        self._legend.setObjectName("cardHint")
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setWordWrap(True)
        self._legend.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._legend.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        mh.addWidget(self._legend, 1)
        mv.addWidget(mix_row)
        v.addWidget(mix_card)
        return page

    # ---- 工具页 ----
    def _build_tools_page(self) -> QWidget:
        """工具页：滚动区 + 卡片分组。

        旧版问题：直接塞进 QVBoxLayout，窄窗口下路径被截断、按钮没有样式。
        """
        outer = QScrollArea(self)
        outer.setWidgetResizable(True)
        outer.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget(outer)
        outer.setWidget(page)
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 2, 0)
        v.setSpacing(8)

        def section(title: str, desc: str, buttons: list[tuple[str, str, object]]):
            """一个分组卡片：标题 + 说明 + 按钮行。

            buttons: [(按钮文字, tone(''|'ok'|'danger'), 槽函数)]
            """
            card = QFrame(page)
            card.setObjectName("cardFlat")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(12, 10, 12, 12)
            cv.setSpacing(6)

            label = QLabel(title, card)
            label.setObjectName("sectionTitle")
            cv.addWidget(label)

            hint = QLabel(desc, card)
            hint.setObjectName("cardHint")
            hint.setWordWrap(True)
            cv.addWidget(hint)

            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 0)
            row.setSpacing(8)
            for text, tone, slot in buttons:
                btn = QPushButton(text, card)
                btn.setProperty("btn", "outline")
                if tone:
                    btn.setProperty("tone", tone)
                btn.setMinimumHeight(28)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(slot)
                row.addWidget(btn)
            row.addStretch(1)
            cv.addLayout(row)
            v.addWidget(card)
            return card

        section("CSV 导入 / 导出",
                "列：日期、金额、类型、分类、用途、备注。导出带 BOM，Excel 可直接打开。",
                [("导出 CSV", "", self.export_csv),
                 ("导入 CSV", "", self.import_csv)])

        section("备份 / 恢复",
                "备份把全部账目导出为 JSON；恢复是追加合并，不会清空现有数据。",
                [("导出备份", "", self.export_backup),
                 ("从备份恢复", "", self.restore_backup)])

        section("日记媒体清理",
                "删除没有被子日记引用的图片与视频（旧版「先上传后保存」会留下垃圾文件）。"
                "被日记引用的文件绝不会删。",
                [("扫描孤立文件", "", lambda: self.cleanup_media(dry_run=True)),
                 ("清理孤立文件", "danger", lambda: self.cleanup_media(dry_run=False))])

        # 数据位置：单行省略显示，完整路径进 tooltip（不能让长路径撑宽布局）
        data_card = QFrame(page)
        data_card.setObjectName("cardFlat")
        dv = QVBoxLayout(data_card)
        dv.setContentsMargins(12, 10, 12, 12)
        dv.setSpacing(4)
        data_title = QLabel("数据位置", data_card)
        data_title.setObjectName("sectionTitle")
        dv.addWidget(data_title)
        data_hint = QLabel(_shorten_path(self._db.path), data_card)
        data_hint.setObjectName("cardHint")
        data_hint.setMinimumWidth(0)
        data_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        data_hint.setToolTip(self._db.path)
        data_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dv.addWidget(data_hint)
        v.addWidget(data_card)

        v.addStretch(1)
        return outer

    # ------------------------------------------------------------ 外部接口
    @property
    def db(self) -> LedgerDatabase:
        return self._db

    def refresh(self) -> None:
        """整页刷新（切到本页/数据变动后调用）。"""
        self._budget_edit.blockSignals(True)
        self._budget_edit.setValue(self._db.budget())
        self._budget_edit.blockSignals(False)
        self.refresh_table()
        self._trend.refresh()
        self._trend_title.setText(f"近 {self._trend.days} 天支出")
        self._donut.set_slices(self._month)
        self._render_legend()

    def refresh_table(self) -> None:
        """按当前筛选条件重建表格与概览。"""
        rows = self._db.list_transactions(
            month=self._month,
            date_from=self._date_from.date().toString("yyyy-MM-dd") if self._range_on.isChecked() else None,
            date_to=self._date_to.date().toString("yyyy-MM-dd") if self._range_on.isChecked() else None,
            category=CATEGORY_NAMES[self._filter_cat.currentIndex() - 1]
            if self._filter_cat.currentIndex() > 0 else None,
            tx_type=self._filter_type.currentData() or "all",
            keyword=self._search.text(),
        )
        self._model.set_rows(rows)
        self._result_label.setText(f"{len(rows)} 笔")
        show_empty = not rows
        self._empty.setVisible(show_empty)
        self._table.setVisible(not show_empty)
        self._btn_edit.setEnabled(not show_empty)
        self._btn_delete.setEnabled(not show_empty)
        self._render_summary()

    def set_month(self, month: str) -> None:
        self._picker.set_month(month)

    # ------------------------------------------------------------ 内部渲染
    def _render_summary(self) -> None:
        summary = self._db.month_summary(self._month)
        self._total_label.setText(money(summary.expense))
        self._count_label.setText(f"{summary.count} 笔")
        budget = self._db.budget()
        rate = int(round(summary.expense / budget * 100)) if budget else 0
        self._budget_rate.setText(f"{rate}%")
        self._budget_bar.setValue(max(0, min(100, rate)))
        self._budget_bar.setToolTip(f"已用 {rate}%（预算 {money(budget)}）")
        highest = (f"最高单笔 {money(summary.highest.amount)}"
                   f"（{summary.highest.purpose}）") if summary.highest else "最高单笔 暂无记录"
        self._extra_label.setText(
            f"日均 {money(summary.average)}（按 {summary.days} 天计算）· "
            f"收入 {money(summary.income)} · {highest}")

        # 预算超支提示
        over = rate > 100
        self._budget_rate.setProperty("over", "true" if over else "false")
        self._budget_rate.setStyleSheet(
            f"color:{color('expense')};font-weight:600;" if over else "")

    def _render_legend(self) -> None:
        totals = self._db.category_totals(self._month)
        total = sum(amount for _n, amount in totals)
        if not totals or total <= 0:
            self._legend.setText('<span style="color:%s">本月还没有支出记录</span>'
                                 % color("faint"))
            return
        lines = []
        for name, amount in totals[:5]:
            percent = int(round(amount / total * 100))
            lines.append(
                f'<div style="margin-bottom:3px">'
                f'<span style="color:{category_color(name)}">●</span> {name} '
                f'<b>{money(amount)}</b> · {percent}%</div>')
        self._legend.setText("".join(lines))

    # ---------------------------------------------------------------- 事件
    def _on_month_changed(self, month: str) -> None:
        self._month = month
        self.refresh_table()
        self._donut.set_slices(month)
        self._render_legend()

    def _on_subtab(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        if idx == 1:
            self._trend.refresh()
            self._donut.set_slices(self._month)
            self._render_legend()

    def _on_range_toggled(self, on: bool) -> None:
        self._date_from.setEnabled(on)
        self._date_to.setEnabled(on)
        self._date_row.setVisible(on)      # 勾选后才展开起止日期
        self._picker.setEnabled(not on)
        self.refresh_table()

    def _on_trend_range(self, idx: int) -> None:
        days = TREND_RANGES[max(0, min(idx, len(TREND_RANGES) - 1))][0]
        self._trend.set_days(days)
        self._trend_title.setText(f"近 {days} 天支出")

    def _on_budget_changed(self) -> None:
        self._db.set_budget(self._budget_edit.value())
        self._render_summary()
        self._notify(f"月预算已更新为 {money(self._db.budget())}")

    def _notify(self, text: str, level: str = "ok") -> None:
        """把提示交给主窗口（自己不是顶层窗口，往父级找 toast）。"""
        win = self.window()
        if hasattr(win, "toast"):
            win.toast(text, level)

    # ---- 增删改 ----
    def add_transaction(self) -> None:
        dialog = TransactionDialog(self._db, self.window())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self._notify("已记下一笔")

    def _selected_row(self) -> Transaction | None:
        indexes = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not indexes:
            return None
        return self._model.row_at(indexes[0].row())

    def _edit_selected(self) -> None:
        tx = self._selected_row()
        if tx is None:
            self._notify("请先选中一笔账目", "error")
            return
        dialog = TransactionDialog(self._db, self.window(), tx)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self._notify("账目已更新")

    def _delete_selected(self) -> None:
        tx = self._selected_row()
        if tx is None:
            self._notify("请先选中一笔账目", "error")
            return
        self._delete_transaction(tx)

    def _delete_transaction(self, tx: Transaction) -> None:
        answer = QMessageBox.question(
            self.window(), "删除账目",
            f"确定删除这笔账目吗？\n\n{tx.date} · {tx.category} · {tx.purpose}\n"
            f"{money(tx.amount, sign=True, tx_type=tx.type)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._db.delete_transaction(tx.id):
            self.refresh()
            self._notify("账目已删除")
        else:
            self._notify("删除失败：账目已不存在", "error")

    def _on_table_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        self._table.selectRow(index.row())
        menu = QMenu(self)
        act_edit = QAction("编辑", menu)
        act_edit.triggered.connect(self._edit_selected)
        menu.addAction(act_edit)
        act_delete = QAction("删除", menu)
        act_delete.triggered.connect(self._delete_selected)
        menu.addAction(act_delete)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ---- CSV ----
    def export_csv(self) -> None:
        default = os.path.join(os.path.expanduser("~"),
                               f"清楚账本-{today_key()}.csv")
        path, _ = QFileDialog.getSaveFileName(self.window(), "导出 CSV", default,
                                              "CSV 文件 (*.csv)")
        if not path:
            return
        rows = self._db.list_transactions()
        try:
            # utf-8-sig：带 BOM，Excel 打开不乱码；newline="" 防多余空行
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["日期", "金额", "类型", "分类", "用途", "备注"])
                for tx in rows:
                    # 修旧版缺陷：写出中文类型，导入时不会再被误判成支出
                    writer.writerow([tx.date, f"{tx.amount:.2f}",
                                     "收入" if tx.is_income else "支出",
                                     tx.category, tx.purpose, tx.note])
        except OSError as exc:
            self._notify(f"导出失败：{exc}", "error")
            return
        self._notify(f"已导出 {len(rows)} 笔到 {os.path.basename(path)}")

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.window(), "导入 CSV", "",
                                              "CSV 文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        try:
            items = parse_csv(path)
        except OSError as exc:
            self._notify(f"读取失败：{exc}", "error")
            return
        if not items:
            self._notify("文件里没有可导入的数据（需含表头行）", "error")
            return
        inserted, skipped = self._db.import_transactions(items)
        if inserted:
            self.refresh()
        message = f"已导入 {inserted} 笔"
        if skipped:
            message += f"，跳过 {skipped} 笔（日期/金额/分类不合规）"
        self._notify(message, "ok" if inserted else "error")

    # ---- 备份 ----
    def export_backup(self) -> None:
        default = os.path.join(os.path.expanduser("~"),
                               f"清楚账本备份-{today_key()}.json")
        path, _ = QFileDialog.getSaveFileName(self.window(), "导出备份", default,
                                              "JSON 文件 (*.json)")
        if not path:
            return
        rows = self._db.list_transactions()
        payload = {
            "transactions": [
                {"date": t.date, "amount": t.amount, "type": t.type,
                 "category": t.category, "purpose": t.purpose, "note": t.note}
                for t in rows
            ],
            "budget": self._db.budget(),
            "exportedAt": today_key(),
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            self._notify(f"备份失败：{exc}", "error")
            return
        self._notify(f"已备份 {len(rows)} 笔到 {os.path.basename(path)}")

    def restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.window(), "从备份恢复", "",
                                              "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                parsed = json.load(fh)
        except (OSError, ValueError) as exc:
            self._notify(f"备份文件读取失败：{exc}", "error")
            return
        items = parsed.get("transactions") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            self._notify("备份文件格式无效：缺少 transactions 数组", "error")
            return
        inserted, skipped = self._db.import_transactions(items)
        # 预算也一并恢复（旧版只恢复账目，这里补上）
        budget = parsed.get("budget")
        if isinstance(budget, (int, float)) and budget > 0:
            self._db.set_budget(float(budget))
        if inserted:
            self.refresh()
        message = f"已恢复 {inserted} 笔"
        if skipped:
            message += f"，跳过 {skipped} 笔"
        self._notify(message, "ok" if inserted else "error")

    # ---- 媒体清理 ----
    def cleanup_media(self, dry_run: bool) -> None:
        if dry_run:
            orphans = self._db.cleanup_orphan_media(dry_run=True)
            if not orphans:
                self._notify("没有发现孤立的媒体文件")
            else:
                self._notify(f"发现 {len(orphans)} 个孤立文件，可点「清理孤立文件」删除")
            return
        orphans = self._db.cleanup_orphan_media(dry_run=True)
        if not orphans:
            self._notify("没有发现孤立的媒体文件")
            return
        answer = QMessageBox.question(
            self.window(), "清理孤立文件",
            f"将删除 {len(orphans)} 个没有被任何日记引用的文件：\n\n"
            + "\n".join(orphans[:6]) + ("\n…" if len(orphans) > 6 else "")
            + "\n\n被日记引用的文件不会被删除。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self._db.cleanup_orphan_media(dry_run=False)
        self._notify(f"已清理 {len(removed)} 个孤立文件")


# =============================================================== CSV 解析
def parse_csv(path: str) -> list[dict]:
    """解析旧版格式 CSV，返回可直接交给 import_transactions 的字典列表。

    兼容两种列布局（与旧版一致）：
        ≥6 列：日期, 金额, 类型, 分类, 用途, 备注
        <6 列：日期, 金额, 分类, 用途, 备注（旧格式，类型固定支出）

    类型映射修掉了旧版缺陷：旧版只认「收入」，导致自己导出的
    ``income`` 再导入时变成支出；这里中英文都认。
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return []
    items: list[dict] = []
    for index, row in enumerate(rows):
        if not row or all(not str(cell).strip() for cell in row):
            continue
        if index == 0:
            continue        # 表头不校验内容，直接跳过
        cells = [str(c).strip() for c in row]
        if len(cells) >= 6:
            day, amount, raw_type, category, purpose, note = cells[:6]
        elif len(cells) >= 5:
            day, amount, category, purpose, note = cells[:5]
            raw_type = "支出"
        else:
            continue
        items.append({
            "date": day,
            "amount": amount,
            "type": TYPE_INCOME if raw_type in ("收入", "income", "in") else TYPE_EXPENSE,
            "category": category or CATEGORY_FALLBACK,
            "purpose": purpose,
            "note": note,
        })
    return items
