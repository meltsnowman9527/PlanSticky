"""单实例保护：第二次启动时把提示发给已运行实例并退出。

Windows 下基于 QLocalServer（命名管道），同一用户会话内互斥。
用环境变量 PLANSTICKY_SINGLE=0 可禁用（测试用）。

竞态处理：首实例冷启动期间第二个进程可能 connect 超时；这里先重试多次，
确认无实例后才 removeServer + listen；若 listen 仍失败（被人抢先），
同样按“已有实例”处理，绝不双开。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from plansticky.config import SINGLE_INSTANCE_KEY

CONNECT_ATTEMPTS = 6      # 每次 200ms，共约 1.2s 重试窗口
CONNECT_TIMEOUT_MS = 200


class SingleInstance(QObject):
    """is_first == True 表示本进程是唯一实例。

    若已有实例在运行：
        - 自动向对方发送 'show'（对方收到后会把便签窗口带到前台）；
        - is_first = False，调用方应直接退出。
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.is_first = False
        self.on_activate = None  # 由 app.py 注入：窗口 show_and_raise

        if os.environ.get("PLANSTICKY_SINGLE") == "0":
            self.is_first = True
            return

        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

        # ---- 先尝试联系已有实例（重试几次，容忍首实例冷启动）----
        for _ in range(CONNECT_ATTEMPTS):
            if self._notify_existing():
                return                      # 已通知对方，is_first 保持 False
            time.sleep(0.05)

        # ---- 确认没有实例：清理残留后抢占监听 ----
        QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        self.is_first = self._server.listen(SINGLE_INSTANCE_KEY)

    def _notify_existing(self) -> bool:
        """连上已有实例并发送 show。成功返回 True。"""
        sock = QLocalSocket(self)
        sock.connectToServer(SINGLE_INSTANCE_KEY)
        if not sock.waitForConnected(CONNECT_TIMEOUT_MS):
            sock.abort()
            return False
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        sock.abort()
        return True

    # ---- 服务端：接收别的实例的 show ----
    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: self._handle(conn))
        conn.disconnected.connect(self._handle)

    def _handle(self, conn=None) -> None:
        try:
            if conn is not None and conn.bytesAvailable():
                conn.readAll()
        except RuntimeError:
            pass
        if self.on_activate is not None:
            self.on_activate()
        if conn is not None:
            try:
                conn.disconnectFromServer()
            except RuntimeError:
                pass
