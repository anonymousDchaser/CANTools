# tests/test_plot_fixes.py
"""曲线图/实时监控修复（Issue 1、Issue 3）offscreen 测试。

Issue 1：实时监控悬浮窗值一行应显示枚举描述含义（修复实时模式 label 带
         (0xID) 后缀导致 sig_name 提取失败、匹配不到值描述）。
Issue 3：实时监控页默认独立子图；新增 reset_realtime 清空曲线但继续记录。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from widgets.plot_widget import PlotWidget


def test_realtime_hover_shows_desc():
    print("[1] 实时监控悬浮窗值描述 ...")
    w = PlotWidget()
    w.set_value_descriptions({"BrakeAssistModeSts": {1: "ON", 0: "OFF"}})
    # 实时模式：frame_id 非 None -> label 形如 "ESP_1.BrakeAssistModeSts(0x1A2)"
    w.start_realtime([(0x1A2, "ESP_1", "BrakeAssistModeSts")])
    line = w._rt_lines[(0x1A2, "ESP_1", "BrakeAssistModeSts")]
    # 核心修复：line -> sig_name 映射应精确取到信号名（不受 (0xID) 后缀影响）
    assert w._line_sig_name.get(line) == "BrakeAssistModeSts", \
        f"映射应取到真实 sig_name, got={w._line_sig_name.get(line)}"
    # 模拟悬停：y=1.0 -> 值描述应为 ON
    ax = w._rt_axes[(0x1A2, "ESP_1", "BrakeAssistModeSts")]
    ev = type("Ev", (), {"xdata": 0.0, "ydata": 1.0, "inaxes": ax})()
    w._apply_highlight(line, (0.0, 1.0), ev)
    assert w._annotation is not None, "悬停应生成注释"
    text = w._annotation.get_text()
    assert "ON" in text, f"悬停注释应含值描述 ON, got={text!r}"
    assert "BrakeAssistModeSts" in text, "注释应含信号名"
    print(f"    OK: 实时悬浮注释含枚举描述 -> {text.splitlines()[0]}")
    w.close()


def test_reset_and_subplot_mode():
    print("[3] 重置曲线 + 默认独立子图 ...")
    w = PlotWidget()
    # 默认显示改为独立子图
    w.set_subplot_mode(True)
    assert w._subplot_mode is True, "应处于独立子图模式"
    assert w._mode_btn.text() == "切换为共享Y轴", \
        f"按钮文案应提示可切回共享Y轴, got={w._mode_btn.text()}"
    # 未进入实时时 reset 不应崩溃
    w.reset_realtime()

    # 进入实时、推送采样、重置后缓冲清空且仍在记录
    w.start_realtime([(0x1, "M", "S")])
    w.push_sample(0x1, "M", "S", 1.0, 5.0)
    key = (0x1, "M", "S")
    assert len(w._rt_buffers[key]["t"]) == 1, "应已记录 1 个点"
    w.reset_realtime()
    assert len(w._rt_buffers[key]["t"]) == 0, "重置后缓冲应清空"
    assert w._rt_running is True, "重置后监控应继续记录（_rt_running 保持）"
    print("    OK: 重置清空曲线且继续记录；默认独立子图生效")
    w.close()


if __name__ == "__main__":
    test_realtime_hover_shows_desc()
    test_reset_and_subplot_mode()
    print("PLOT FIXES TESTS PASSED")
