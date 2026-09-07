"""主题系统：浅色 / 深色 / 跟随系统。

- 颜色一律用 #AARRGGBB（8 位十六进制）字符串，QSS 与自绘控件都能直接解析。
- “跟随系统”读取 Windows 注册表 AppsUseLightTheme，并每 8 秒轮询一次，
  系统切换深浅色时便签自动跟随（离线、无网络、零依赖）。
- 换主题 = 重新生成 QSS + QPalette 应用到整个应用 + 全控件重绘。
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# ------------------------------------------------------------------ 调色板
LIGHT = {
    "bg":            "#FFFFFF",          # 窗口底色
    "panel":         "#F1F1EF",          # 输入框/分段控件轨道
    "text":          "#37352F",
    "sub":           "#787774",          # 次级文字
    "faint":         "#9B9A97",          # 弱文字（计数、空态）
    "doneText":      "#B0AFAA",          # 已完成任务文字
    "line":          "#1F37352F",        # 10-12% 分隔线/边框
    "lineStrong":    "#5237352F",        # 虚线边框
    "hover":         "#0D37352F",        # 悬停底色
    "hoverStrong":   "#1437352F",
    "accent":        "#2383E2",
    "accentSoft":    "#242383E2",        # 选中/焦点淡蓝底
    "accentText":    "#FFFFFF",
    "danger":        "#E5484D",
    "dangerSoft":    "#1FE5484D",
    "checkBorder":   "#6637352F",
    "scroll":        "#59787774",
    "menuBg":        "#FFFFFF",
    "tooltipBg":     "#3F3F3E",
    "tooltipText":   "#F5F5F4",
    "emptyHint":     "#9B9A97",
}

DARK = {
    "bg":            "#212121",
    "panel":         "#2C2C2C",
    "text":          "#E6E6E6",
    "sub":           "#A8A8A8",
    "faint":         "#7C7C7C",
    "doneText":      "#5F5F5F",
    "line":          "#1AFFFFFF",
    "lineStrong":    "#33FFFFFF",
    "hover":         "#0FFFFFFF",
    "hoverStrong":   "#14FFFFFF",
    "accent":        "#5E9BFF",
    "accentSoft":    "#265E9BFF",
    "accentText":    "#FFFFFF",
    "danger":        "#FF6B70",
    "dangerSoft":    "#26FF6B70",
    "checkBorder":   "#80FFFFFF",
    "scroll":        "#59FFFFFF",
    "menuBg":        "#2B2B2B",
    "tooltipBg":     "#3A3A3A",
    "tooltipText":   "#F5F5F4",
    "emptyHint":     "#7C7C7C",
}

# 当前生效的配色（自绘控件在 paintEvent 里实时读取，主题切换后整窗重绘）
CURRENT: dict = LIGHT
CURRENT_IS_DARK = False


def color(key: str) -> str:
    return CURRENT.get(key, "#FF00FF")  # 亮品红 = 拼写错误，便于发现


# ------------------------------------------------------------------ QSS
def build_qss(c: dict) -> str:
    """按配色生成整份 QSS。占位符用 %(key)s，避免与 CSS 花括号冲突。"""
    return """
QWidget { background: transparent; }
QWidget#rootWin { background: %(bg)s; }

/* ---- 输入框 ---- */
QLineEdit {
    background: %(panel)s; color: %(text)s;
    border: 1px solid %(line)s; border-radius: 7px;
    padding: 5px 8px;
    selection-background-color: %(accent)s; selection-color: %(accentText)s;
}
QLineEdit:focus { border: 1px solid %(accent)s; }
QLineEdit#rowEditor { background: transparent; border: none; padding: 0 2px; }

/* ---- 滚动条（细、无横向）---- */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 4px; min-height: 26px; }
QScrollBar::handle:vertical:hover { background: %(faint)s; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0 2px; }
QScrollBar::handle:horizontal { background: %(scroll)s; border-radius: 4px; min-width: 26px; }
QScrollBar::handle:horizontal:hover { background: %(faint)s; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- 菜单 / 提示 ---- */
QMenu {
    background: %(menuBg)s; color: %(text)s;
    border: 1px solid %(line)s; border-radius: 8px; padding: 4px;
}
QMenu::item { background: transparent; border-radius: 6px;
    padding: 6px 26px 6px 10px; color: %(text)s; }
QMenu::item:selected { background: %(hover)s; }
QMenu::item:disabled { color: %(faint)s; }
QMenu::separator { height: 1px; background: %(line)s; margin: 4px 8px; }
QToolTip {
    background: %(tooltipBg)s; color: %(tooltipText)s;
    border: 1px solid %(line)s; border-radius: 6px; padding: 4px 8px;
}

/* ---- 按钮 ---- */
QPushButton { background: transparent; border: none; color: %(text)s; }
QPushButton#addBtn {
    border: 1px dashed %(lineStrong)s; border-radius: 8px;
    color: %(sub)s; padding: 7px; font-size: 13px;
}
QPushButton#addBtn:hover { background: %(hover)s; color: %(text)s; border-color: %(faint)s; }
QLabel#sectionLabel { font-size: 11px; color: %(faint)s; padding-left: 8px; }
QLabel#dayCount { font-size: 11px; color: %(faint)s; }
QLabel#emptyHint { color: %(faint)s; }
QLabel#mutedLabel { font-size: 12px; color: %(sub)s; }

/* ---- 多日列表视图 ---- */
QPushButton#listDayHead {
    text-align: left; padding: 4px 8px; border-radius: 6px;
    font-size: 13px; color: %(text)s; background: transparent;
}
QPushButton#listDayHead:hover { background: %(hover)s; }
QPushButton#listDayHead[today="true"] { color: %(accent)s; font-weight: 600; }
QPushButton#listDayHead[empty="true"] { color: %(faint)s; font-weight: 400; }
QPushButton#listDayHead[today="true"][empty="true"] { color: %(accent)s; }
QPushButton[cls="icon"] { border-radius: 6px; color: %(sub)s; }
QPushButton[cls="icon"]:hover { background: %(hover)s; color: %(text)s; }
QPushButton[cls="icon"]:checked { color: %(accent)s; background: %(accentSoft)s; }
QPushButton[cls="icon"][role="danger"]:hover { background: %(dangerSoft)s; color: %(danger)s; }
QPushButton[cls="nav"] { border-radius: 6px; color: %(sub)s; }
QPushButton[cls="nav"]:hover { background: %(hover)s; color: %(text)s; }
QPushButton[cls="date"] { border-radius: 6px; color: %(text)s; padding: 2px 6px; font-size: 13px; }
QPushButton[cls="date"]:hover { background: %(hover)s; }
QPushButton[cls="today"] { border-radius: 7px; color: %(accent)s; padding: 2px 9px; font-size: 12px; }
QPushButton[cls="today"]:hover { background: %(accentSoft)s; }
QPushButton[cls="today"]:disabled { color: %(faint)s; }
QPushButton[cls="seg"] { border-radius: 6px; color: %(sub)s; padding: 4px 12px; font-size: 13px; }
QPushButton[cls="seg"]:hover { color: %(text)s; }
QPushButton[cls="seg"]:checked { background: %(accentSoft)s; color: %(accent)s; }
QFrame#segTrack { background: %(panel)s; border-radius: 8px; }

/* ---- 对话框按钮 ---- */
QDialog QPushButton, QMessageBox QPushButton {
    background: %(panel)s; border: 1px solid %(line)s; border-radius: 7px;
    padding: 5px 16px; color: %(text)s;
}
QDialog QPushButton:hover, QMessageBox QPushButton:hover { border-color: %(faint)s; background: %(hover)s; }

/* ---- 日历 ---- */
QCalendarWidget QWidget { background: %(bg)s; color: %(text)s; }
QCalendarWidget QToolButton {
    background: transparent; color: %(text)s;
    border-radius: 6px; padding: 4px; font-size: 13px;
}
QCalendarWidget QToolButton:hover { background: %(hover)s; }
QCalendarWidget QAbstractItemView {
    background: %(bg)s; color: %(text)s; outline: none;
    selection-background-color: %(accentSoft)s; selection-color: %(accent)s;
    font-size: 12px;
}
QCalendarWidget QAbstractItemView:disabled { color: %(faint)s; }
QCalendarWidget QSpinBox {
    background: %(panel)s; color: %(text)s; border: 1px solid %(line)s; border-radius: 6px;
}
""" % c


def build_palette(c: dict) -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    p.setColor(QPalette.ColorRole.Base, QColor(c["bg"]))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(c["panel"]))
    p.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.Button, QColor(c["panel"]))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["faint"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accentText"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["tooltipBg"]))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(c["tooltipText"]))
    return p


# ------------------------------------------------------------------ 管理器
class ThemeManager(QObject):
    """负责主题切换与“跟随系统”轮询。"""

    changed = Signal(str)  # 发出当前选择：'light' | 'dark' | 'system'

    def __init__(self, db, parent: QObject | None = None):
        super().__init__(parent)
        self._db = db
        self._key = db.get_setting("theme", "system")
        if self._key not in ("light", "dark", "system"):
            self._key = "system"
        self._last_resolved = None
        self._timer: QTimer | None = None
        self._apply()
        self._ensure_follow_timer()

    # ---- 对外接口 ----
    @property
    def key(self) -> str:
        return self._key

    def set_key(self, key: str) -> None:
        if key not in ("light", "dark", "system"):
            return
        if key == self._key and self._key != "system":
            return
        self._key = key
        self._db.set_setting("theme", key)
        self._apply()
        self._ensure_follow_timer()
        self.changed.emit(key)

    # ---- 内部 ----
    def _resolve_scheme(self) -> str:
        """把用户选择解析成实际 light/dark。"""
        if self._key == "light":
            return "light"
        if self._key == "dark":
            return "dark"
        return self._system_is_dark() and "dark" or "light"

    @staticmethod
    def _system_is_dark() -> bool:
        """读 Windows 注册表：AppsUseLightTheme=0 表示深色。"""
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False

    def _apply(self) -> None:
        global CURRENT, CURRENT_IS_DARK
        scheme = self._resolve_scheme()
        CURRENT = LIGHT if scheme == "light" else DARK
        CURRENT_IS_DARK = scheme == "dark"
        self._last_resolved = scheme
        app = QApplication.instance()
        if app is None:
            return
        app.setPalette(build_palette(CURRENT))
        app.setStyleSheet(build_qss(CURRENT))
        # 强制所有控件按新样式重绘（自绘控件在 paintEvent 里读 CURRENT）
        for w in app.allWidgets():
            w.update()

    def _ensure_follow_timer(self) -> None:
        if self._key != "system":
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(8000)
            self._timer.timeout.connect(self._poll_system)
            self._timer.start()
        self._poll_system()

    def _poll_system(self) -> None:
        resolved = self._resolve_scheme()
        if resolved != self._last_resolved:
            self._apply()

    def shutdown(self) -> None:
        if self._timer is not None:
            self._timer.stop()


def set_app_font(app: QApplication) -> None:
    """设置全局字体：优先中文 UI 字体，10pt 保证可读性。"""
    from PySide6.QtGui import QFontDatabase
    families = QFontDatabase.families()
    for cand in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        if cand in families:
            f = QFont(cand)
            f.setPointSize(10)
            app.setFont(f)
            return
    f = app.font()
    f.setPointSize(10)
    app.setFont(f)
