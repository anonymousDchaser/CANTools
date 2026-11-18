# -*- coding: utf-8 -*-
"""生成 app.icns（macOS 应用图标）。

用法（只能在 macOS 上执行）::

    python3 tools/make_icns.py              # 有 app.png 就用它，否则画默认图标
    python3 tools/make_icns.py --src x.png  # 指定源图
    python3 tools/make_icns.py --keep       # 保留中间 iconset 目录便于排查

为什么只能在 macOS 上生成：
    ``iconutil`` 是 macOS 自带工具，Windows 上没有等价命令行实现；ICNS 容器
    格式虽可手写，但 Apple 对 512/1024 档位有特定要求，手写容易做出「能装
    不能用」的图标，得不偿失。

为什么用 PyQt5 缩放而不是 Pillow：
    本项目的依赖里没有 Pillow，为生成图标额外引入一个包不划算；
    Qt 的 QPixmap 缩放（SmoothTransformation）在各平台表现一致。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Apple 要求的 iconset 文件名与像素尺寸；@2x 档位必须提供，
# 否则 Retina 屏与「程序坞放大」会用到低分辨率图，显示发虚。
ICONSET = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

# 源图候选（根目录优先）；都没有时用内置绘制
SOURCE_CANDIDATES = ("app.png", "app.icns.png", os.path.join("picture", "app.png"))


def _draw_default(px: int):
    """内置默认图标：深色圆角底 + 青色 CAN 波形 + "CAN" 字样。

    没有提供源图时用它，保证 .app 不会是空白图标；需要品牌图标时把
    app.png 放到项目根目录重跑本脚本即可。
    """
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import (
        QColor, QFont, QPainter, QPainterPath, QPen, QPixmap,
    )

    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    radius = px * 0.22
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1e1e2e"))
    p.drawRoundedRect(QRectF(0, 0, px, px), radius, radius)

    # 一条简化的 CAN 差分波形（两路反相），占图标下半部
    pen = QPen(QColor("#4fc3f7"))
    pen.setWidthF(max(1.0, px * 0.045))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    mid = px * 0.70
    amp = px * 0.10
    path = QPainterPath()
    path.moveTo(px * 0.12, mid)
    for i, frac in enumerate((0.28, 0.40, 0.52, 0.64, 0.76, 0.88)):
        y = mid - amp if i % 2 == 0 else mid + amp * 0.2
        path.lineTo(px * frac, y)
    p.drawPath(path)

    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(8, int(px * 0.34)))
    p.setFont(font)
    p.setPen(QColor("#e0e0e0"))
    p.drawText(QRectF(0, px * 0.22, px, px * 0.38), Qt.AlignCenter, "CAN")

    p.end()
    return pm


def _load_source(path: str):
    from PyQt5.QtGui import QPixmap
    pm = QPixmap(path)
    return pm if not pm.isNull() else None


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 macOS 的 app.icns")
    ap.add_argument("--src", default="", help="源图（PNG/JPG），缺省自动查找 app.png")
    ap.add_argument("--keep", action="store_true", help="保留 AppIcon.iconset")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("app.icns 只能在 macOS 上生成（依赖 iconutil），当前系统: "
              f"{sys.platform}")
        return 1
    if not shutil.which("iconutil"):
        print("未找到 iconutil，请确认使用的是 macOS 自带 Python 或已装 Xcode CLT")
        return 1

    # QPixmap 需要 QGuiApplication（不显示窗口，offscreen 也可）
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841

    src = args.src
    if not src:
        for cand in SOURCE_CANDIDATES:
            p = os.path.join(ROOT, cand)
            if os.path.exists(p):
                src = p
                break

    base = None
    if src:
        if not os.path.isabs(src):
            src = os.path.join(ROOT, src)
        base = _load_source(src)
        if base is None:
            print(f"源图无法读取: {src}，改用内置默认图标")
    if base is None:
        print("未找到源图 app.png，使用内置默认图标")

    iconset = os.path.join(ROOT, "AppIcon.iconset")
    if os.path.isdir(iconset):
        shutil.rmtree(iconset)
    os.makedirs(iconset)

    from PyQt5.QtCore import Qt
    for name, size in ICONSET:
        if base is not None:
            pm = base.scaled(size, size, Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
        else:
            pm = _draw_default(size)
        if not pm.save(os.path.join(iconset, name), "PNG"):
            print(f"写入失败: {name}")
            return 1
    print(f"已渲染 {len(ICONSET)} 个尺寸 -> {iconset}")

    target = os.path.join(ROOT, "app.icns")
    rc = subprocess.run(["iconutil", "-c", "icns", iconset, "-o", target]).returncode
    if rc != 0:
        print("iconutil 失败")
        return rc

    if not args.keep:
        shutil.rmtree(iconset, ignore_errors=True)
    print(f"已生成 {target}（{os.path.getsize(target)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
