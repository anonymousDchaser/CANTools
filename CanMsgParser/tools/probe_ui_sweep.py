"""UI 交互巡检：走遍所有页签与面板，抓取任何 PyQt6 重载不匹配 / 虚函数异常。

PyQt6 里虚函数（paint/event）抛出的 Python 异常无法传回 Python 侧，最终 abort()，
表现为「窗口没画面、退出码 0xC0000409」。因此这里把 Qt 消息与 Python 异常统一落盘，
任何一条 PYEXC 都视为缺陷。
"""
import os
import sys
import traceback

ROOT = r"D:/lzxDChaserGithub/IDEATools/CanMsgParser"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

LOG = os.path.join(ROOT, ".workbuddy", "ui_sweep.log")
_f = open(LOG, "w", encoding="utf-8", buffering=1)


def _emit(tag, text):
    _f.write("[%s] %s\n" % (tag, text))
    _f.flush()


from PyQt6.QtCore import qInstallMessageHandler, Qt, QPoint, QPointF  # noqa: E402

qInstallMessageHandler(lambda mode, ctx, msg: _emit("QT", "%s | %s" % (mode, msg)))
sys.excepthook = lambda t, v, tb: _emit("PYEXC", "".join(traceback.format_exception(t, v, tb)))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from utils.ui_scale import init_ui_scale  # noqa: E402

init_ui_scale()

import main_window as mw  # noqa: E402


def step(fn, label):
    try:
        fn()
        app.processEvents()
    except Exception:
        _emit("PYEXC", "%s\n%s" % (label, traceback.format_exc()))


w = mw.MainWindow()
w.show()
app.processEvents()
_emit("STEP", "main window shown")

tabs = w._tabs
for i in range(tabs.count()):
    def _switch(i=i):
        tabs.setCurrentIndex(i)
        tabs.widget(i).grab()
        for child in tabs.widget(i).findChildren(object):
            pass
    step(_switch, "switch tab %d (%s)" % (i, tabs.tabText(i)))

# 停靠面板
for dock in w.findChildren(type(w._search_dock)):
    step(lambda d=dock: d.grab(), "grab dock %s" % dock.windowTitle())

panel = w._group_panel
tree = panel._sig_list


def _expand():
    for i in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(i)
        it.setExpanded(True)
        it.setExpanded(False)


step(_expand, "expand/collapse group items")


def _toggle_eye():
    for i in range(tree.topLevelItemCount()):
        panel._sig_list.set_eye_visible(i, False)
        panel._sig_list.set_eye_visible(i, True)


step(_toggle_eye, "toggle eye icons")


def _search():
    panel._sig_search.setText("a")
    panel._sig_search.setText("")
    panel._sig_search.setText("Sig")


step(_search, "group search")


def _switch_group():
    if panel._group_combo.count() > 1:
        panel._on_group_changed(1)
        panel._on_group_changed(0)


step(_switch_group, "switch group")

# 曲线图：绘制 + 悬停
plot = w._plot_widget


def _plot_grab():
    plot._canvas.draw()
    plot.grab()


step(_plot_grab, "plot draw+grab")

# 报文表：选中首行
for name in ("_msg_table", "_message_table"):
    tbl = getattr(w, name, None)
    if tbl is not None:
        def _sel(t=tbl):
            if t.rowCount():
                t.selectRow(0)
        step(_sel, "select message row (%s)" % name)

app.processEvents()
_emit("STEP", "sweep done")
w.close()
