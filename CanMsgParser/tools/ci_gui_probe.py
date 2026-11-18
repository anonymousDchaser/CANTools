# -*- coding: utf-8 -*-
"""CI 诊断：定位 macOS 上 QDockWidget.setWidget() 卡死的最小复现场景。

为什么单独写一个脚本：
    卡住的是 Qt C++ 内部的布局循环，Python 侧 faulthandler 抓到的栈永远停在
    ``self._group_dock.setWidget(...)`` 这一行，再往里没有 Python 帧可用，
    所以无法靠栈区分「dock 本身有问题」还是「放进去的控件有问题」。
    这里给每个最小场景起一个独立子进程，谁卡住谁就是责任方。

用法（本地也可以跑，只是本地通常全过）::

    python tools/ci_gui_probe.py
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CASES = [
    (
        "A-dock-plain",
        "QDockWidget + 裸 QWidget",
        "win = QMainWindow(); d = QDockWidget('t', win); d.setWidget(QWidget())",
    ),
    (
        "B-dock-group",
        "QDockWidget + SignalGroupPanel",
        "from widgets.signal_group_panel import SignalGroupPanel\n"
        "win = QMainWindow(); d = QDockWidget('t', win); d.setWidget(SignalGroupPanel())",
    ),
    (
        "C-group-only",
        "只构造并 show SignalGroupPanel（不进 dock）",
        "from widgets.signal_group_panel import SignalGroupPanel\n"
        "p = SignalGroupPanel(); p.show()",
    ),
    (
        "D-dock-search",
        "QDockWidget + SignalTreeWidget",
        "from widgets.signal_tree import SignalTreeWidget\n"
        "win = QMainWindow(); d = QDockWidget('t', win); d.setWidget(SignalTreeWidget())",
    ),
]

TEMPLATE = """import faulthandler, sys
faulthandler.dump_traceback_later(20, exit=True)
sys.path.insert(0, {root!r})
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
            print("    " + line)
        if not ok:
            failed += 1
    print(f"\n{len(CASES) - failed}/{len(CASES)} 个场景通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
