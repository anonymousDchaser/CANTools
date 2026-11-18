# -*- coding: utf-8 -*-
"""PyQt5 → PyQt6 机械迁移（幂等，可重复执行）。

为什么自己写：
    PyQt 没有官方 PyQt5→PyQt6 转换器；Qt 官方的 qt5to6 只处理 C++，而且
    Qt5Compat 是 C++ 模块（PyQt6 未绑定），兜不住旧枚举写法。

做的事：
    1) ``from PyQt5`` / ``PyQt5.`` → ``PyQt6``
    2) ``exec_()`` → ``exec()``
    3) 枚举全限定：``Qt.AlignLeft`` → ``Qt.AlignmentFlag.AlignLeft``、
       ``QListWidget.NoSelection`` → ``QAbstractItemView.SelectionMode.NoSelection``。
       映射表按 **MRO 继承链**生成：直接写死宿主名不够，PyQt6 里继承来的枚举
       （QListWidget 用的 NoSelection 实际定义在 QAbstractItemView）在子类上访问会
       AttributeError，必须回到定义它的那个基类取全限定路径。
    4) matplotlib 后端 ``backend_qt5agg`` → ``backend_qtagg``。

不处理（人工）：QAction/QShortcut 归属 QtGui、Qt6 已移除的高 DPI 属性、行为差异。

用法::

    python tools/qt5toqt6.py --check
    python tools/qt5toqt6.py
"""
from __future__ import annotations

import argparse
import enum
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402


def collect_classes():
    out = []
    for mod in (QtCore, QtGui, QtWidgets):
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and (name == 'Qt' or name.startswith('Q')):
                out.append(obj)
    return out


def own_enums(cls):
    """该类**自身**定义的枚举：成员名 → 'Class.EnumClass.Member'。"""
    table = {}
    for attr, value in getattr(cls, '__dict__', {}).items():
        if not (isinstance(value, type) and issubclass(value, enum.Enum)):
            continue
        for member in value.__members__:
            table[member] = f'{cls.__name__}.{attr}.{member}'
    return table


def build_host_map(classes):
    """每个 Q* 类 → {成员名: 全限定路径}，沿 MRO 合并（子类覆盖基类）。"""
    host_map = {}
    for cls in classes:
        table = {}
        for base in reversed(cls.__mro__):
            table.update(own_enums(base))
        host_map.setdefault(cls.__name__, table)
    return host_map


def iter_files():
    seen = set()
    for pattern in ('*.py', 'widgets/*.py', 'core/*.py', 'utils/*.py',
                    'workers/*.py', 'tests/*.py', 'tools/*.py'):
        for path in glob.glob(os.path.join(ROOT, pattern)):
            if os.path.basename(path) == 'qt5toqt6.py' or path in seen:
                continue
            seen.add(path)
            yield path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只统计不写入')
    args = ap.parse_args()

    host_map = build_host_map(collect_classes())
    host_re = re.compile(r'\b(Qt|Q[A-Za-z0-9_]+)\.([A-Z][A-Za-z0-9_]*)\b(?!\.)')

    stats = {'module': 0, 'enum': 0, 'exec': 0, 'backend': 0}
    touched = []
    unknown = set()

    for path in iter_files():
        raw = open(path, 'rb').read()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            continue
        original = text

        n = text.count('from PyQt5') + text.count('PyQt5.')
        if n:
            text = text.replace('from PyQt5', 'from PyQt6').replace('PyQt5.', 'PyQt6.')
            stats['module'] += n

        def _sub(m):
            host, member = m.group(1), m.group(2)
            table = host_map.get(host)
            if table and member in table:
                stats['enum'] += 1
                return table[member]
            # 只对疑似 Qt 宿主记录未命中，避免把普通类属性算进来
            if host == 'Qt' or (host.startswith('Q') and host[1:2].isupper()):
                unknown.add(f'{host}.{member}')
            return m.group(0)

        text = host_re.sub(_sub, text)

        # Qt6 把颜色常量收进 GlobalColor，且成员名是全小写，上面的大写正则匹配不到：
        # Qt.transparent -> Qt.GlobalColor.transparent
        def _sub_color(m):
            stats['enum'] += 1
            return 'Qt.GlobalColor.%s' % m.group(1)

        text = re.sub(r'\bQt\.(transparent|white|black|red|green|blue|cyan|magenta|yellow|gray|darkRed|darkGreen|darkBlue|darkCyan|darkMagenta|darkYellow|darkGray|lightGray)\b(?!\.)', _sub_color, text)

        n = text.count('exec_()')
        if n:
            text = text.replace('exec_()', 'exec()')
            stats['exec'] += n

        for old, new in (('backend_qt5agg', 'backend_qtagg'), ('backend_qt5', 'backend_qt')):
            n = text.count(old)
            if n:
                text = text.replace(old, new)
                stats['backend'] += n

        if text != original:
            touched.append(os.path.relpath(path, ROOT))
            if not args.check:
                open(path, 'wb').write(text.encode('utf-8'))

    print('宿主类:', len(host_map))
    print('替换统计:', stats)
    print('涉及文件:', len(touched))
    if unknown:
        print('未命中的 Host.Member（可能是普通属性，也可能是漏映射，需人工确认）:')
        for u in sorted(unknown)[:40]:
            print('   ', u)
    return 0


if __name__ == '__main__':
    main()
