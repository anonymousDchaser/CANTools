"""抓取 Qt 致命消息（qFatal/qWarning）与未捕获异常。

0xC0000409 是 abort()，PyQt6 在虚函数中抛出未处理异常时会 qFatal 直接终止进程，
此时 stderr 可能被吞掉。这里用 qInstallMessageHandler 把消息立即落盘。
"""
import os
import sys
import traceback

ROOT = r"D:/lzxDChaserGithub/IDEATools/CanMsgParser"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, ".workbuddy", "qtmsg.log")
_f = open(LOG, "w", encoding="utf-8", buffering=1)


def _emit(tag, text):
    _f.write("[%s] %s\n" % (tag, text))
    _f.flush()


from PyQt6.QtCore import qInstallMessageHandler  # noqa: E402


def handler(mode, ctx, msg):
    _emit("QT", "%s | %s" % (mode, msg))


qInstallMessageHandler(handler)


def excepthook(t, v, tb):
    _emit("PYEXC", "".join(traceback.format_exception(t, v, tb)))


sys.excepthook = excepthook

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QTimer  # noqa: E402

app = QApplication([])
_emit("BOOT", "QApplication ok")

from utils.ui_scale import init_ui_scale  # noqa: E402

init_ui_scale()
_emit("BOOT", "ui_scale ok")

import main_window as mw  # noqa: E402

w = mw.MainWindow()
_emit("BOOT", "MainWindow ok, items=%d" % w._group_panel._sig_list.topLevelItemCount())

QTimer.singleShot(1500, app.quit)
w.show()
_emit("BOOT", "show() returned")
app.exec()
_emit("BOOT", "exec returned OK")
