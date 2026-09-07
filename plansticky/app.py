"""应用组装：把 QApplication / 数据库 / 主题 / 主窗口 / 托盘 / 单实例 接起来。

入口顺序（main.py 调用本模块的 main）：
    1. 设置 Windows AppUserModelID（任务栏图标分组正确）
    2. 创建 QApplication、全局字体、程序图标
    3. 单实例检查（已有实例 -> 通知其弹窗并退出）
    4. 打开数据库 -> 主题 -> 主窗口 -> 托盘
    5. 运行事件循环；退出时统一清理

环境变量（供测试/调试，正常使用无需设置）：
    PLANSTICKY_DATA_DIR   数据目录覆盖
    PLANSTICKY_SINGLE=0   跳过单实例保护
    PLANSTICKY_AUTOQUIT_MS  启动 N 毫秒后自动退出（GUI 冒烟测试用）
"""
from __future__ import annotations

import os
import sys
import traceback


def _set_app_user_model_id() -> None:
    """Windows 任务栏按 AppUserModelID 分组图标，避免显示成 python.exe。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PlanSticky.App.1")
    except Exception:      # noqa: BLE001 非关键
        pass


def _install_excepthook() -> None:
    """未捕获异常 -> 写入数据目录 error.log 并弹窗提示（避免静默崩溃）。"""
    from PySide6.QtWidgets import QApplication, QMessageBox
    from plansticky import config

    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(config.log_path(), "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        app = QApplication.instance()
        if app is not None:
            QMessageBox.critical(
                None, "计划便签 出错了",
                f"程序遇到问题，已记录到：\n{config.log_path()}\n\n{exc_value}")
            app.quit()
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    _set_app_user_model_id()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from plansticky import config, icons
    from plansticky.database import Database
    from plansticky.main_window import MainWindow
    from plansticky.singleinstance import SingleInstance
    from plansticky.theme import ThemeManager, set_app_font
    from plansticky.tray import create_tray

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(config.APP_DISPLAY_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setOrganizationName("PlanSticky")
    app.setQuitOnLastWindowClosed(False)
    set_app_font(app)
    app.setWindowIcon(icons.make_app_icon(64))
    _install_excepthook()

    # 单实例：已有实例则让它显示窗口，本进程直接退出
    si = SingleInstance()
    if not si.is_first:
        return 0

    db = Database(config.db_path())
    theme = ThemeManager(db)
    win = MainWindow(db, theme)
    si.on_activate = win.show_and_raise

    tray = create_tray(app, win, db, theme)
    win.attach_tray(tray)
    if tray is not None:
        # 任何一天的数据变动后刷新托盘“今日 x/y”提示
        win._day_view.countChanged.connect(lambda *_: tray.refresh_tooltip())
        tray.refresh_tooltip()

    def _cleanup():
        theme.shutdown()
        win._save_geometry_now()
        if tray is not None:
            tray.hide()
        db.close()

    app.aboutToQuit.connect(_cleanup)

    win.show()

    # 测试钩子：GUI 冒烟测试用（PLANSTICKY_AUTOQUIT_MS=1500 python main.py）
    auto = os.environ.get("PLANSTICKY_AUTOQUIT_MS")
    if auto and auto.isdigit():
        QTimer.singleShot(int(auto), app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
