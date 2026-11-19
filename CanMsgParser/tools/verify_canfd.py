"""CAN FD 端到端验证：连接页勾选 → 共享总线以 FD 建链 → 64 字节帧可发可收。"""
import os
import sys
import traceback

ROOT = r"D:/lzxDChaserGithub/IDEATools/CanMsgParser"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, ".workbuddy", "fd_probe.log")
_f = open(LOG, "w", encoding="utf-8", buffering=1)


def emit(tag, text):
    _f.write("[%s] %s\n" % (tag, text))
    _f.flush()


from PyQt6.QtCore import qInstallMessageHandler  # noqa: E402

qInstallMessageHandler(lambda mode, ctx, msg: emit("QT", "%s | %s" % (mode, msg)))
sys.excepthook = lambda t, v, tb: emit("PYEXC", "".join(traceback.format_exception(t, v, tb)))

from PyQt6.QtWidgets import QApplication  # noqa: E402

import can  # noqa: E402

app = QApplication([])

from utils.ui_scale import init_ui_scale  # noqa: E402

init_ui_scale()

import main_window as mw  # noqa: E402
from core.can_utils import get_fd_mode  # noqa: E402

w = mw.MainWindow()
emit("STEP", "main window built")

conn = w._conn_widget
idx = conn._device_keys.index("virtual")
conn._device_combo.setCurrentIndex(idx)
conn._channel_combo.setCurrentText("fdprobe")
conn._fd_check.setChecked(True)
conn._dbr_combo.setCurrentText("2000000")
emit("STEP", "ui fd=%s dbr=%s" % (conn.get_fd_enabled(), conn.get_data_bitrate()))
emit("STEP", "global fd mode=%s" % (get_fd_mode(),))

try:
    conn._on_connect()
except Exception:
    emit("PYEXC", "on_connect\n" + traceback.format_exc())

app.processEvents()
cfg = w._conn_manager.get_config() if hasattr(w, "_conn_manager") else None
emit("STEP", "manager config=%s" % (cfg,))
bus = w._conn_manager.get_bus() if hasattr(w, "_conn_manager") else None
emit("STEP", "bus proto=%s" % (bus.protocol if bus else None))

# 独立接收端（同通道）验证 64 字节 FD 帧确实上线
rx, err = can.Bus(interface="virtual", channel="fdprobe", protocol=can.CanProtocol.CAN_FD), None
if bus is not None:
    bus.send(can.Message(arbitration_id=0x321, data=bytes(range(64)), is_fd=True))
    got = rx.recv(timeout=1.0)
    emit("STEP", "fd tx/rx: got=%s is_fd=%s len=%s" % (
        got is not None, got.is_fd if got else None, len(got.data) if got else None))
rx.shutdown()
emit("STEP", "DONE")
