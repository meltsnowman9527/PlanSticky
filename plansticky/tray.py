"""系统托盘。

菜单：打开便签 / 今日计划 / 置顶 / 主题 / 退出程序
行为：关闭主窗口时隐藏到托盘；单/双击托盘图标唤起。
托盘不可用时整个功能自动跳过（close 按钮改为直接退出）。
"""
from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from plansticky import config


class TrayIcon(QSystemTrayIcon):
    """封装托盘图标与菜单，直接绑定主窗口/主题/数据库。"""

    def __init__(self, icon: QIcon, win, db, theme_mgr):
        super().__init__(icon)
        self._win = win
        self._db = db
        self._theme = theme_mgr
        self._shown_msg = False

        menu = QMenu()
        act_open = QAction("打开便签", menu)
        act_open.triggered.connect(self._win.show_and_raise)
        menu.addAction(act_open)

        act_today = QAction("今日计划", menu)
        act_today.triggered.connect(self._win.show_today)
        menu.addAction(act_today)

        menu.addSeparator()

        self._act_pin = QAction("置顶", menu)
        self._act_pin.setCheckable(True)
        self._act_pin.setChecked(self._win.pinned)
        self._act_pin.triggered.connect(
            lambda on: self._win.set_pinned(on, persist=True))
        self._win.pinChanged.connect(self._act_pin.setChecked)
        menu.addAction(self._act_pin)

        sub_theme = menu.addMenu("主题")
        group = QActionGroup(sub_theme)
        group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for key, label in (("light", "浅色"), ("dark", "深色"), ("system", "跟随系统")):
            act = QAction(label, sub_theme)
            act.setCheckable(True)
            act.setChecked(self._theme.key == key)
            act.triggered.connect(lambda _=False, k=key: self._theme.set_key(k))
            group.addAction(act)
            sub_theme.addAction(act)
            self._theme_actions[key] = act
        self._theme.changed.connect(self._sync_theme_check)

        menu.addSeparator()

        act_quit = QAction("退出程序", menu)
        act_quit.triggered.connect(self._win.quit_app)
        menu.addAction(act_quit)

        self.setContextMenu(menu)
        self.setToolTip(config.APP_DISPLAY_NAME)
        self.activated.connect(self._on_activated)
        self.refresh_tooltip()

    def _sync_theme_check(self, key: str) -> None:
        for k, act in self._theme_actions.items():
            act.setChecked(k == key)

    # ---- 行为 ----
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._win.show_and_raise()

    def refresh_tooltip(self) -> None:
        """今日完成/总数放进悬停提示（如 计划便签 · 今日 2/5）。"""
        key = QDate.currentDate().toString("yyyy-MM-dd")
        done, total = self._db.counts("day", key)
        if total:
            tip = f"{config.APP_DISPLAY_NAME} · 今日 {done}/{total}"
        else:
            tip = config.APP_DISPLAY_NAME
        self.setToolTip(tip)

    def notify_hidden_once(self) -> None:
        if not self._shown_msg:
            self._shown_msg = True
            self.showMessage(config.APP_DISPLAY_NAME,
                             "已最小化到托盘，双击图标或点“打开便签”恢复。",
                             QSystemTrayIcon.MessageIcon.Information, 3000)


def create_tray(app, win, db, theme_mgr):
    """创建托盘（系统不支持时返回 None）。"""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    from plansticky import icons
    tray = TrayIcon(icons.make_app_icon(48), win, db, theme_mgr)
    tray.show()
    return tray
