# tests/test_message_autoconnect.py
"""实时报文页「连接即自动监听」测试（offscreen，无需硬件）。

覆盖问题 1：连接上 CAN 后，报文页应自动开始监听并显示收到的报文；断开后自动停止。
"""
import sys
import time

from PyQt5.QtWidgets import QApplication

import can
from core.can_connection import CanConnectionManager
from widgets.realtime_message_widget import RealtimeMessageWidget


def main():
    app = QApplication(sys.argv)
    mgr = CanConnectionManager()
    w = RealtimeMessageWidget()
    w.set_connection_manager(mgr)  # 接入连接状态信号
    # 主窗口会在连接前把设备配置同步到各页（_on_connection_changed -> set_connection）
    w.set_connection("virtual", "vch_msg", 500000)

    # 连接 -> 报文页应自动开始监听（start_capture）
    bus, err = mgr.connect("virtual", "vch_msg", 500000)
    assert bus is not None, err
    app.processEvents()  # 让 state_changed 的同步槽执行完
    assert w._capturing, "连接建立后报文页应自动开始监听"
    assert mgr._listeners, "报文页应已注册为共享总线监听者"
    print("OK: 连接后报文页自动监听并显示")

    # 外部下发一帧 -> 报文页应显示该行
    tx = can.Bus(interface="virtual", channel="vch_msg")
    tx.send(can.Message(arbitration_id=0x300, data=b"\x11\x22", is_extended_id=False))
    time.sleep(0.3)
    app.processEvents()
    assert 0x300 in w._rows, f"报文页应显示收到的帧, rows={list(w._rows)}"
    print(f"OK: 报文页显示外部下发帧 0x{0x300:X}")

    # 断开 -> 报文页应自动停止监听
    mgr.disconnect()
    app.processEvents()
    assert not w._capturing, "断开后报文页应自动停止监听"
    print("OK: 断开后报文页自动停止监听")

    print("MESSAGE AUTOCONNECT TEST PASSED")


if __name__ == "__main__":
    main()
