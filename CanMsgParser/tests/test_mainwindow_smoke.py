# tests/test_mainwindow_smoke.py
"""主窗口级冒烟：验证共享连接管理器接线 + 连接状态页真正建总线（虚拟通道）。"""
import sys
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

# 必须在创建 QApplication 后导入（部分模块依赖 Qt 应用上下文）
import main_window
from core.can_connection import CanConnectionManager


def main():
    app = QApplication(sys.argv)
    mw = main_window.MainWindow()

    # 1) 管理器已创建并注入三页
    assert isinstance(mw._conn_manager, CanConnectionManager)
    assert mw._conn_widget._manager is mw._conn_manager
    assert mw._sim_widget._manager is mw._conn_manager
    assert mw._monitor_widget._manager is mw._conn_manager
    assert mw._realtime_msg_widget._manager is mw._conn_manager
    print("OK: 共享连接管理器已注入连接状态/模拟上报/实时监控/实时报文四页")

    # 2) 连接状态页切到虚拟通道并真正建立共享总线
    dev = mw._conn_widget._device_combo
    vt_idx = dev.findData("virtual")
    assert vt_idx >= 0
    dev.setCurrentIndex(vt_idx)
    mw._conn_widget._on_connect()
    assert mw._conn_manager.is_connected, "连接状态页『连接 CAN』应建立共享总线"
    print("OK: 连接状态页『连接 CAN』已建立共享总线（虚拟通道）")

    # 3) 模拟页「开始」走自动连接复用同一总线（不重复 Initialize）
    bus_before = mw._conn_manager.get_bus()
    # 直接验证 ensure_connected 幂等（等价于模拟页 _ensure_bus 的内部调用）
    bus_after, err = mw._conn_manager.ensure_connected("virtual", "virtual", 500000)
    assert bus_after is bus_before and err is None
    print("OK: 功能页自动连接复用同一共享总线（不会二次 Initialize）")

    # 4) 断开：停止三页 + 关闭共享总线
    mw._conn_widget._on_disconnect()
    assert not mw._conn_manager.is_connected, "断开后应关闭共享总线"
    print("OK: 『断开 CAN』已关闭共享总线")

    # 5) 再次自动连接（模拟“忘了点连接”后点动作）：从断开态自动建总线
    bus2, err2 = mw._conn_manager.ensure_connected("virtual", "virtual", 500000)
    assert bus2 is not None and err2 is None
    print("OK: 断开后功能页动作可自动重新连接（问题2 场景）")

    mw.close()
    print("MAINWINDOW SMOKE PASSED")


if __name__ == "__main__":
    main()
