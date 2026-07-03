# tests/test_message_table_integration.py
"""报文表格在 MainWindow 上下文中的集成测试

验证 message_table 在主窗口加载 DBC 和日志文件的实际流程中能否正常展开解码。
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from widgets.message_table import MessageTableWidget
from core.dbc_parser import parse_dbc

# 创建全局 QApplication（PyQt5 要求）
_app = None

def get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


@pytest.fixture
def app():
    """提供 QApplication 实例"""
    return get_app()


@pytest.fixture
def message_table(app):
    """创建 MessageTableWidget 实例"""
    widget = MessageTableWidget()
    yield widget
    widget.deleteLater()


@pytest.fixture
def sample_frame_index():
    """创建示例帧索引 DataFrame，模拟 log_loader 的输出格式"""
    return pd.DataFrame({
        "frame_id": np.array([0, 1, 2, 3, 4], dtype=np.int64),
        "timestamp": np.array([0.0, 0.001, 0.002, 0.003, 0.004], dtype=np.float64),
        "arbitration_id": np.array([0x1A0, 0x1A1, 0x1A0, 0x1A1, 0x1A0], dtype=np.uint32),
        "dlc": np.array([8, 8, 8, 8, 8], dtype=np.uint8),
        "channel": np.array([0, 0, 0, 0, 0], dtype=np.int32),
        "is_fd": np.array([False, False, False, False, False], dtype=bool),
    })


@pytest.fixture
def sample_raw_data():
    """创建示例原始数据数组"""
    data = np.zeros((5, 8), dtype=np.uint8)
    # EngineData (0x1A0): EngineRPM=1000 (0x03E8), EngineSpeed=100 (0x0064), EngineTemp=80 (0x50 + 40 offset)
    data[0] = [0xE8, 0x03, 0x64, 0x00, 0x78, 0x00, 0x00, 0x00]
    # TransmissionData (0x1A1): GearPosition=3, TransTemp=60 (0x3C + 40 offset)
    data[1] = [0x03, 0x00, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00]
    data[2] = [0xE8, 0x03, 0x64, 0x00, 0x78, 0x00, 0x00, 0x00]
    data[3] = [0x03, 0x00, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00]
    data[4] = [0xE8, 0x03, 0x64, 0x00, 0x78, 0x00, 0x00, 0x00]
    return data


@pytest.fixture
def dbc_path():
    """返回测试 DBC 文件路径"""
    return os.path.join(os.path.dirname(__file__), "fixtures", "test.dbc")


class TestPathADbcFirstThenLog:
    """路径A：先加载 DBC，再加载日志"""

    def test_expand_after_dbc_then_log(self, message_table, sample_frame_index, sample_raw_data, dbc_path):
        """模拟主窗口先加载DBC再加载日志的流程"""
        # 步骤1：模拟 _load_dbc() -> update_dbc()（此时表格无数据）
        message_table.update_dbc(dbc_path)
        assert message_table._db is not None, "update_dbc 后 _db 应该已加载"

        # 步骤2：模拟 _on_load_finished() -> set_data()
        messages = parse_dbc(dbc_path)
        message_table.set_data(sample_frame_index, sample_raw_data, messages, dbc_path)

        # 验证表格已填充
        assert message_table._tree.topLevelItemCount() == 5, "应该有5行数据"
        assert message_table._db is not None, "set_data 后 _db 应该已加载"

        # 步骤3：尝试展开第一行（EngineData 0x1A0）
        item = message_table._tree.topLevelItem(0)
        assert item is not None, "第一行应该存在"

        # 手动触发展开处理
        message_table._on_item_expanded(item)

        # 验证子项已添加
        assert item.childCount() > 0, f"展开后应有子项，但实际 childCount={item.childCount()}"

        # 验证子项包含信号名
        child_texts = [item.child(i).text(1) for i in range(item.childCount())]
        assert "EngineRPM" in child_texts, f"应包含 EngineRPM 信号，实际: {child_texts}"


class TestPathBLogFirstThenDbc:
    """路径B：先加载日志，再加载 DBC"""

    def test_expand_after_log_then_dbc(self, message_table, sample_frame_index, sample_raw_data, dbc_path):
        """模拟主窗口先加载日志再加载DBC的流程"""
        # 步骤1：模拟 _on_load_finished() -> set_data()（dbc_path 为空）
        messages = []  # 日志加载时可能还没有解析 DBC
        message_table.set_data(sample_frame_index, sample_raw_data, messages, "")

        # 验证表格已填充
        assert message_table._tree.topLevelItemCount() == 5, "应该有5行数据"
        assert message_table._db is None, "未提供 dbc_path 时 _db 应为 None"

        # 验证每行都有占位子项（使展开箭头可见）
        item = message_table._tree.topLevelItem(0)
        assert item.childCount() == 1, "应有占位子项"
        assert "点击展开" in item.child(0).text(1), "占位符应提示可展开"

        # 步骤2：尝试展开（应该显示错误提示，因为无DBC）
        message_table._on_item_expanded(item)
        assert item.childCount() == 1, "应有一个错误提示子项"
        assert "未加载 DBC" in item.child(0).text(1), "应提示未加载 DBC"

        # 步骤3：模拟 _load_dbc() -> update_dbc()
        message_table.update_dbc(dbc_path)
        assert message_table._db is not None, "update_dbc 后 _db 应该已加载"

        # 步骤4：update_dbc 应该清除旧子项并重新添加占位符
        assert item.childCount() == 1, "update_dbc 后应有占位子项"
        assert "点击展开" in item.child(0).text(1), "应重新添加占位符"

        # 步骤5：再次展开（现在应该能成功解码）
        message_table._on_item_expanded(item)
        assert item.childCount() > 0, f"重新展开后应有子项，但实际 childCount={item.childCount()}"

        # 验证子项包含信号名（不再是占位符或错误提示）
        child_texts = [item.child(i).text(1) for i in range(item.childCount())]
        assert "EngineRPM" in child_texts, f"应包含 EngineRPM 信号，实际: {child_texts}"
        assert not any("点击展开" in t for t in child_texts), "不应再有占位符"


class TestMainWindowIntegration:
    """完整模拟 MainWindow 的加载流程"""

    def test_main_window_flow_dbc_first(self, app, sample_frame_index, sample_raw_data, dbc_path):
        """模拟 MainWindow 先加载 DBC 再加载日志的完整流程"""
        from main_window import MainWindow

        window = MainWindow()

        try:
            # 模拟 _load_dbc() 的核心逻辑
            messages = parse_dbc(dbc_path)
            window._messages = messages
            window._dbc_path = dbc_path
            window._signal_tree.load_messages(messages)
            window._bit_layout.load_messages(messages)
            window._group_panel.set_messages(messages)
            window._message_table.update_dbc(dbc_path)

            # 验证 message_table 状态
            assert window._message_table._db is not None, "update_dbc 后 _db 应已加载"

            # 模拟 _on_load_finished() 的核心逻辑
            window._frame_index = sample_frame_index
            window._raw_data = sample_raw_data
            window._message_table.set_data(
                sample_frame_index, sample_raw_data,
                window._messages, window._dbc_path
            )

            # 验证表格状态
            assert window._message_table._tree.topLevelItemCount() == 5
            assert window._message_table._db is not None

            # 尝试展开第一行
            item = window._message_table._tree.topLevelItem(0)
            window._message_table._on_item_expanded(item)

            # 关键断言：展开必须成功
            assert item.childCount() > 0, (
                f"FAILED: 无法展开行！childCount={item.childCount()}, "
                f"_db={window._message_table._db is not None}, "
                f"_dbc_path='{window._message_table._dbc_path}'"
            )

            # 验证解码结果
            child_texts = [item.child(i).text(1) for i in range(item.childCount())]
            assert "EngineRPM" in child_texts, f"解码结果应包含 EngineRPM，实际: {child_texts}"

            print("PASSED: MainWindow 集成测试通过（先DBC后日志）")

        finally:
            window.close()
            window.deleteLater()

    def test_main_window_flow_log_first(self, app, sample_frame_index, sample_raw_data, dbc_path):
        """模拟 MainWindow 先加载日志再加载 DBC 的完整流程

        注意：MainWindow.__init__ 可能自动加载上次保存的 DBC（从配置文件），
        所以 _db 可能已经不为 None。测试重点验证展开解码功能正常工作。
        """
        from main_window import MainWindow

        window = MainWindow()

        try:
            # 模拟 _on_load_finished() 的核心逻辑
            window._frame_index = sample_frame_index
            window._raw_data = sample_raw_data
            window._message_table.set_data(
                sample_frame_index, sample_raw_data,
                [], ""  # 空的 messages 和 dbc_path
            )

            # 验证表格已填充
            assert window._message_table._tree.topLevelItemCount() == 5

            # 验证每行有占位子项（关键：确保展开箭头可见）
            item = window._message_table._tree.topLevelItem(0)
            assert item.childCount() == 1, "应有占位子项使展开箭头可见"

            # 模拟 _load_dbc() 的核心逻辑
            messages = parse_dbc(dbc_path)
            window._messages = messages
            window._dbc_path = dbc_path
            window._signal_tree.load_messages(messages)
            window._bit_layout.load_messages(messages)
            window._group_panel.set_messages(messages)
            window._message_table.update_dbc(dbc_path)

            # 验证 DBC 已加载
            assert window._message_table._db is not None

            # 验证 update_dbc 后仍有占位符（保持可展开状态）
            assert item.childCount() == 1, "update_dbc 后应有占位子项"

            # 尝试展开第一行
            window._message_table._on_item_expanded(item)

            # 关键断言：展开必须成功解码
            assert item.childCount() > 0, (
                f"FAILED: 无法展开行！childCount={item.childCount()}"
            )

            # 验证解码结果包含信号名
            child_texts = [item.child(i).text(1) for i in range(item.childCount())]
            assert "EngineRPM" in child_texts, f"解码结果应包含 EngineRPM，实际: {child_texts}"

            print("PASSED: MainWindow 集成测试通过（先日志后DBC）")

        finally:
            window.close()
            window.deleteLater()


class TestFilterAndExpand:
    """验证过滤后展开功能正常"""

    def test_expand_after_filter(self, message_table, sample_frame_index, sample_raw_data, dbc_path):
        """应用过滤后，展开功能仍应正常工作"""
        # 设置数据和DBC
        messages = parse_dbc(dbc_path)
        message_table.set_data(sample_frame_index, sample_raw_data, messages, dbc_path)

        # 应用ID过滤（只显示 0x1A0）
        message_table._id_filter.setCurrentText("0x1A0")
        message_table._apply_filter()

        # 验证过滤后行数减少
        filtered_count = message_table._tree.topLevelItemCount()
        assert filtered_count == 3, f"过滤后应有3行(0x1A0)，实际 {filtered_count}"

        # 验证每行仍有占位符
        item = message_table._tree.topLevelItem(0)
        assert item.childCount() == 1, "过滤后每行应有占位子项"

        # 展开应正常工作
        message_table._on_item_expanded(item)
        assert item.childCount() > 0, "过滤后展开应有子项"

        child_texts = [item.child(i).text(1) for i in range(item.childCount())]
        assert "EngineRPM" in child_texts, f"过滤后展开应包含 EngineRPM，实际: {child_texts}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
