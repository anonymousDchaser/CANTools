# -*- coding: utf-8 -*-
"""CI 诊断：定位 macOS 上 SignalGroupPanel 显示时卡死的最小复现场景。

为什么单独写一个脚本：
    卡住的是 Qt C++ 内部的布局/绘制循环，Python 侧 faulthandler 抓到的栈永远停在
    p.show() 这一行，再往里没有 Python 帧可用，所以靠栈区分不出责任方。
    这里给每个最小场景起一个独立子进程，谁卡住谁就是责任方。

第一轮结论（2026-11）：
    QDockWidget + 裸 QWidget 正常、QDockWidget + SignalTreeWidget 正常，
    但 SignalGroupPanel 只要 show() 就卡（连 dock 都不需要），
    因此责任人锁定在面板自身，与 QDockWidget 无关。

用法::

    python tools/ci_gui_probe.py
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_PANEL = "from widgets.signal_group_panel import SignalGroupPanel\n"

CASES = [
    (
        "A-dock-plain-show",
        "对照：QDockWidget + 裸 QWidget + 窗口 show()",
        "win = QMainWindow(); d = QDockWidget('t', win)\n"
        "d.setWidget(QWidget()); win.addDockWidget(Qt.LeftDockWidgetArea, d); win.show()",
    ),
    (
        "B-panel-construct",
        "只构造 SignalGroupPanel（不 show）",
        _PANEL + "p = SignalGroupPanel()",
    ),
    (
        "C-panel-resize",
        "构造 + resize(420, 640)（不 show）",
        _PANEL + "p = SignalGroupPanel(); p.resize(420, 640)",
    ),
    (
        "D-panel-show",
        "构造 + show()（基线复现）",
        _PANEL + "p = SignalGroupPanel(); p.show()",
    ),
    (
        "E-panel-show-no-autosize",
        "构造 + 把 _autosize_signal_column 换成 no-op + show()",
        _PANEL + "p = SignalGroupPanel(); p._autosize_signal_column = lambda: None; p.show()",
    ),
    (
        "F-panel-show-tree-hidden",
        "构造 + 信号树 hide() + show()",
        _PANEL + "p = SignalGroupPanel(); p._sig_list.hide(); p.show()",
    ),
    (
        "G-qss-only",
        "对照：裸 QWidget 套 DARK_PANEL_QSS + show()",
        "from widgets.theme import DARK_PANEL_QSS\n"
        "w = QWidget(); w.setStyleSheet(DARK_PANEL_QSS); w.resize(420, 640); w.show()",
    ),
    (
        "H-tree-standalone",
        "对照：单独 show 一个 DragReorderTreeWidget（带同样列设置）",
        "from PyQt5.QtWidgets import QHeaderView\n"
        "from widgets.drag_reorder_list import DragReorderTreeWidget\n"
        "t = DragReorderTreeWidget(); t.setColumnCount(2)\n"
        "t.setHeaderLabels(['信号', '备注'])\n"
        "t.header().setSectionResizeMode(0, QHeaderView.Interactive)\n"
        "t.header().setSectionResizeMode(1, QHeaderView.Stretch)\n"
        "t.setColumnWidth(0, 150); t.resize(420, 480); t.show()",
    ),
]

TEMPLATE = """import faulthandler, sys
faulthandler.dump_traceback_later(20, exit=True)
sys.path.insert(0, {root!r})
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QDockWidget, QWidget
app = QApplication([])
{middle}
print('CASE OK', flush=True)
"""


def main() -> int:
    failed = 0
    for name, desc, middle in CASES:
        code = TEMPLATE.format(root=ROOT, middle=middle)
        try:
            r = subprocess.run(
                [sys.executable, "-u", "-c", code],
                capture_output=True, text=True, timeout=45,
            )
            out = (r.stdout + r.stderr).strip()
            ok = r.returncode == 0 and "CASE OK" in out
        except subprocess.TimeoutExpired:
            out, ok = "子进程 45s 未结束（faulthandler 都没机会输出）", False
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {desc}")
        for line in out.splitlines():
            if "Populating font family aliases" in line:
                continue
            print("    " + line)
        if not ok:
            failed += 1
    print(f"\n{len(CASES) - failed}/{len(CASES)} 个场景通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
