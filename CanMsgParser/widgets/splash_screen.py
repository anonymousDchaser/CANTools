# widgets/splash_screen.py
"""启动画面 — 带小车和坐标图的优美启动界面"""
import math
from PyQt5.QtWidgets import QSplashScreen, QApplication
from PyQt5.QtGui import (
    QPixmap, QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QPainterPath,
)
from PyQt5.QtCore import Qt, QRectF, QPointF


class SplashScreen(QSplashScreen):
    """自定义启动画面，绘制小车 + 坐标图 + 进度"""

    SPLASH_W = 600
    SPLASH_H = 380

    def __init__(self):
        # 创建透明背景的 pixmap
        pixmap = QPixmap(self.SPLASH_W, self.SPLASH_H)
        pixmap.fill(Qt.transparent)
        super().__init__(pixmap)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.SplashScreen | Qt.WindowStaysOnTopHint
        )
        self._progress = 0
        self._status_text = "正在初始化..."

        self._draw()

    # ------------------------------------------------------------------
    # 绘图入口
    # ------------------------------------------------------------------
    def _draw(self):
        """重绘整个启动画面"""
        pixmap = QPixmap(self.SPLASH_W, self.SPLASH_H)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. 背景圆角矩形 + 渐变
        self._draw_background(painter)
        # 2. 标题区
        self._draw_title(painter)
        # 3. 小车插图（左下）
        self._draw_car(painter, 80, 220)
        # 4. 坐标图插图（右下）
        self._draw_chart(painter, 340, 210)
        # 5. 进度条
        self._draw_progress(painter)
        # 6. 状态文字
        self._draw_status(painter)

        painter.end()
        self.setPixmap(pixmap)

    # ------------------------------------------------------------------
    # 各区域绘制
    # ------------------------------------------------------------------
    def _draw_background(self, painter: QPainter):
        """深色渐变背景 + 圆角"""
        rect = QRectF(0, 0, self.SPLASH_W, self.SPLASH_H)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.setClipPath(path)

        gradient = QLinearGradient(0, 0, 0, self.SPLASH_H)
        gradient.setColorAt(0, QColor("#1e2a3a"))
        gradient.setColorAt(0.5, QColor("#2b2b2b"))
        gradient.setColorAt(1, QColor("#1a1a2e"))
        painter.fillPath(path, QBrush(gradient))

        # 顶部高光线
        painter.setPen(QPen(QColor("#4fc3f7"), 2))
        painter.drawLine(40, 80, self.SPLASH_W - 40, 80)

    def _draw_title(self, painter: QPainter):
        """应用标题"""
        # 主标题
        painter.setPen(QColor("#4fc3f7"))
        font = QFont("Microsoft YaHei", 22, QFont.Bold)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 20, self.SPLASH_W, 40), Qt.AlignCenter, "CAN 报文分析工具"
        )

        # 副标题
        painter.setPen(QColor("#a0a0a0"))
        font = QFont("Microsoft YaHei", 9)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 58, self.SPLASH_W, 20),
            Qt.AlignCenter,
            "CAN Bus Message Analysis Tool  v1.0.0",
        )

    def _draw_car(self, painter: QPainter, x: int, y: int):
        """绘制简化的小车插图"""
        painter.save()
        painter.translate(x, y)

        accent = QColor("#4fc3f7")

        # 车身（圆角矩形）
        painter.setPen(QPen(accent, 2))
        painter.setBrush(QBrush(QColor("#3a4a5a")))
        painter.drawRoundedRect(QRectF(10, 30, 140, 40), 10, 10)

        # 车顶（梯形）
        roof = QPainterPath()
        roof.moveTo(40, 30)
        roof.lineTo(55, 5)
        roof.lineTo(110, 5)
        roof.lineTo(125, 30)
        roof.closeSubpath()
        painter.drawPath(roof)

        # 车窗
        painter.setBrush(QBrush(QColor("#1e3a5a")))
        win = QPainterPath()
        win.moveTo(48, 28)
        win.lineTo(60, 10)
        win.lineTo(105, 10)
        win.lineTo(117, 28)
        win.closeSubpath()
        painter.drawPath(win)

        # 车轮
        painter.setBrush(QBrush(QColor("#1a1a1a")))
        painter.setPen(QPen(accent, 2))
        painter.drawEllipse(QRectF(25, 60, 28, 28))
        painter.drawEllipse(QRectF(107, 60, 28, 28))

        # 轮毂
        painter.setBrush(QBrush(QColor("#555555")))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(32, 67, 14, 14))
        painter.drawEllipse(QRectF(114, 67, 14, 14))

        # 前灯
        painter.setBrush(QBrush(QColor("#ffeb3b")))
        painter.drawEllipse(QRectF(145, 38, 6, 6))

        # CAN 信号波纹（车上方）
        painter.setPen(QPen(QColor("#4fc3f7"), 1.5))
        painter.setBrush(Qt.NoBrush)
        for i in range(3):
            painter.drawArc(
                QRectF(70 - i * 8, -10 - i * 5, 16 + i * 16, 16 + i * 16),
                0,
                180 * 16,
            )

        painter.restore()

    def _draw_chart(self, painter: QPainter, x: int, y: int):
        """绘制坐标图插图"""
        painter.save()
        painter.translate(x, y)

        w, h = 220, 130
        accent = QColor("#4fc3f7")

        # 坐标轴
        painter.setPen(QPen(QColor("#888888"), 1.5))
        painter.drawLine(10, 10, 10, h - 10)           # Y 轴
        painter.drawLine(10, h - 10, w - 10, h - 10)   # X 轴

        # 网格线
        painter.setPen(QPen(QColor("#333333"), 1, Qt.DashLine))
        for i in range(1, 5):
            y_line = int(10 + i * (h - 20) / 5)
            painter.drawLine(10, y_line, w - 10, y_line)
        for i in range(1, 6):
            x_line = int(10 + i * (w - 20) / 6)
            painter.drawLine(x_line, 10, x_line, h - 10)

        # 曲线 1 — 正弦波（蓝色）
        painter.setPen(QPen(accent, 2.5))
        path1 = QPainterPath()
        for i in range(w - 20):
            px = 10 + i
            py = h / 2 + 30 * math.sin(i * 0.08)
            if i == 0:
                path1.moveTo(px, py)
            else:
                path1.lineTo(px, py)
        painter.drawPath(path1)

        # 曲线 2 — 方波（橙色）
        painter.setPen(QPen(QColor("#ff9800"), 2))
        path2 = QPainterPath()
        for i in range(0, w - 20, 20):
            py = 30 if (i // 20) % 2 == 0 else 70
            if i == 0:
                path2.moveTo(10 + i, py)
            else:
                path2.lineTo(10 + i, py)
        painter.drawPath(path2)

        # 数据点
        painter.setBrush(QBrush(accent))
        painter.setPen(Qt.NoPen)
        for i in range(0, w - 20, 25):
            py = h / 2 + 30 * math.sin(i * 0.08)
            painter.drawEllipse(QPointF(10 + i, py), 3, 3)

        # 轴标签
        painter.setPen(QColor("#888888"))
        painter.setFont(QFont("Microsoft YaHei", 7))
        painter.drawText(QRectF(-5, 5, 20, 15), Qt.AlignCenter, "V")
        painter.drawText(QRectF(w - 15, h - 8, 20, 15), Qt.AlignCenter, "t")

        painter.restore()

    def _draw_progress(self, painter: QPainter):
        """进度条"""
        bar_x, bar_y = 40, self.SPLASH_H - 50
        bar_w, bar_h = self.SPLASH_W - 80, 8

        # 背景槽
        painter.setBrush(QBrush(QColor("#333333")))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 4, 4)

        # 已完成的进度
        if self._progress > 0:
            progress_w = bar_w * self._progress / 100
            gradient = QLinearGradient(bar_x, 0, bar_x + progress_w, 0)
            gradient.setColorAt(0, QColor("#4fc3f7"))
            gradient.setColorAt(1, QColor("#66bb6a"))
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(
                QRectF(bar_x, bar_y, progress_w, bar_h), 4, 4
            )

    def _draw_status(self, painter: QPainter):
        """状态文字 + 百分比 + 版权"""
        # 状态描述
        painter.setPen(QColor("#cccccc"))
        painter.setFont(QFont("Microsoft YaHei", 9))
        painter.drawText(
            QRectF(40, self.SPLASH_H - 75, 400, 20),
            Qt.AlignLeft,
            self._status_text,
        )

        # 百分比数字
        painter.setPen(QColor("#4fc3f7"))
        painter.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        painter.drawText(
            QRectF(self.SPLASH_W - 140, self.SPLASH_H - 75, 100, 20),
            Qt.AlignRight,
            f"{self._progress}%",
        )

        # 底部版权信息
        painter.setPen(QColor("#666666"))
        painter.setFont(QFont("Microsoft YaHei", 7))
        painter.drawText(
            QRectF(40, self.SPLASH_H - 22, self.SPLASH_W - 80, 15),
            Qt.AlignLeft,
            "作者: laizhenxin  |  lzxDchaser@126.com",
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def update_progress(self, progress: int, status: str = ""):
        """更新进度和状态文字并立即重绘"""
        self._progress = max(0, min(100, progress))
        if status:
            self._status_text = status
        self._draw()
        QApplication.processEvents()
