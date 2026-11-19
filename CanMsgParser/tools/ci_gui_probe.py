# -*- coding: utf-8 -*-
"""CI 诊断：定位 macOS 上 SignalGroupPanel 显示时卡死的最小复现场景。

为什么单独写一个脚本：
    卡住的是 Qt C++ 内部的布局/绘制循环，Python 侧 faulthandler 抓到的栈永远停在
    p.show() 这一行，再往里没有 Python 帧可用，所以靠栈区分不出责任方。
    这里给每个最小场景起一个独立子进程，谁卡住谁就是责任方。

已排除（2026-11 两轮结论）：
    - QDockWidget 无关：面板单独 show 就卡；
    - 构造、resize 不卡，卡在 show()；
    - 不是列宽自适应（禁用后仍卡）、不是信号树（hide 后仍卡）、
      不是 DARK_PANEL_QSS（裸 QWidget 套同样样式正常）。

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
        "win = QMainWindow()\n"
        "d = QDockWidget('t', win)\n"
        "d.setWidget(QWidget())\n"
        "win.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d)\n"
        "win.show()\n"
        "print('CASE OK', flush=True)",
    ),
    (
        "B-panel-show",
        "基线：构造 + show()",
        _PANEL + "p = SignalGroupPanel(); p.show()\n"
        "print('CASE OK', flush=True)",
    ),
    (
        "C-panel-hide-children-show",
        "构造 + 隐藏全部直接子控件 + show()",
        _PANEL + "p = SignalGroupPanel()\n"
        "for w in p.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):\n"
        "    w.hide()\n"
        "p.show()\n"
        "print('CASE OK', flush=True)",
    ),
    (
        "D-panel-stepwise-children",
        "构造 + 先隐藏全部子控件 + show，再逐个显示并打点（卡住时最后一行就是责任控件）",
        _PANEL + "from PyQt6.QtCore import QTimer\n"
        "p = SignalGroupPanel()\n"
        "kids = p.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)\n"
        "for w in kids:\n"
        "    w.hide()\n"
        "p.show()\n"
        "print('panel shown, direct children =', len(kids), flush=True)\n"
        "state = {'i': 0}\n"
        "def step():\n"
        "    i = state['i']\n"
        "    if i >= len(kids):\n"
        "        print('ALL CHILDREN SHOWN OK', flush=True)\n"
        "        app.quit()\n"
        "        return\n"
        "    w = kids[i]\n"
        "    print('showing', i, w.__class__.__name__, flush=True)\n"
        "    w.show()\n"
        "    state['i'] = i + 1\n"
        "    QTimer.singleShot(300, step)\n"
        "QTimer.singleShot(300, step)\n"
        "app.exec()\n"
        "print('CASE OK', flush=True)",
    ),
]

TEMPLATE = """import faulthandler, sys
faulthandler.dump_traceback_later(20, exit=True)
sys.path.insert(0, {root!r})
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMainWindow, QDockWidget, QWidget
app = QApplication([])
{middle}
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
