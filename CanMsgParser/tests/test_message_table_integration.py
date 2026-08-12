# tests/test_message_table_integration.py
"""报文表格模型级集成测试

验证 MessageTableModel（QTreeView + QAbstractItemModel 虚拟化）在
"先 DBC 后日志 / 先日志后 DBC / 过滤后展开"三种流程下能否正确展开解码。
不再依赖 QTreeWidget 内部 API（topLevelItem / childCount），改用模型接口断言。
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from widgets.message_table import MessageTableWidget
from core.dbc_parser import parse_dbc

_app = None


def get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


@pytest.fixture
def app():
    return get_app()


@pytest.fixture
def message_table(app):
    w = MessageTableWidget()
    yield w
    w.deleteLater()


def _child_texts(model, frame_idx):
    idx = model.index(frame_idx, 0)
    model.fetchMore(idx)
    n = model.rowCount(idx)
    return [model.data(model.index(i, 1, idx), Qt.DisplayRole) for i in range(n)]


@pytest.fixture
def sample_frame_index():
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
    d = np.zeros((5, 8), dtype=np.uint8)
    d[0] = [0xE8, 0x03, 0x64, 0x00, 0x78, 0x00, 0x00, 0x00]
    d[1] = [0x03, 0x00, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00]
    d[2] = [0xE8, 0x03, 0x64, 0x00, 0x78, 0x00, 0x00, 0x00]
    d[3] = [0x03, 0x00, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00]
    d[4] = [0xE8, 0x03, 0x64, 0x00, 0x78, 0x00, 0x00, 0x00]
    return d


@pytest.fixture
def dbc_path():
    return os.path.join(os.path.dirname(__file__), "fixtures", "test.dbc")


class TestPathADbcFirstThenLog:
    def test_expand_after_dbc_then_log(self, message_table, sample_frame_index, sample_raw_data, dbc_path):
        message_table.update_dbc(dbc_path)
        assert message_table._db is not None, "update_dbc 后 _db 应该已加载"

        messages = parse_dbc(dbc_path)
        message_table.set_data(sample_frame_index, sample_raw_data, messages, dbc_path)

        assert message_table._model.rowCount() == 5, "应该有5行数据"
        assert message_table._db is not None, "set_data 后 _db 应该已加载"

        texts = _child_texts(message_table._model, 0)
        assert "EngineRPM" in texts, f"应包含 EngineRPM 信号，实际: {texts}"


class TestPathBLogFirstThenDbc:
    def test_expand_after_log_then_dbc(self, message_table, sample_frame_index, sample_raw_data, dbc_path):
        message_table.set_data(sample_frame_index, sample_raw_data, [], "")

        assert message_table._model.rowCount() == 5, "应该有5行数据"
        assert message_table._db is None, "未提供 dbc_path 时 _db 应为 None"

        # 无 DBC 时展开 -> 错误子项
        idx = message_table._model.index(0, 0)
        message_table._model.fetchMore(idx)
        assert message_table._model.rowCount(idx) == 1, "应有一个错误提示子项"
        err = message_table._model.data(message_table._model.index(0, 1, idx), Qt.DisplayRole)
        assert "未加载 DBC" in err, "应提示未加载 DBC"

        # 加载 DBC 后重新展开 -> 成功解码
        message_table.update_dbc(dbc_path)
        assert message_table._db is not None, "update_dbc 后 _db 应该已加载"

        idx2 = message_table._model.index(0, 0)
        message_table._model.fetchMore(idx2)
        texts = [message_table._model.data(message_table._model.index(i, 1, idx2), Qt.DisplayRole)
                 for i in range(message_table._model.rowCount(idx2))]
        assert "EngineRPM" in texts, f"应包含 EngineRPM 信号，实际: {texts}"
        assert not any("点击展开" in t for t in texts), "不应再有占位符"


class TestFilterAndExpand:
    def test_expand_after_filter(self, message_table, sample_frame_index, sample_raw_data, dbc_path):
        messages = parse_dbc(dbc_path)
        message_table.set_data(sample_frame_index, sample_raw_data, messages, dbc_path)

        message_table._id_filter.setCurrentText("0x1A0")
        message_table._apply_filter()

        # 0x1A0 出现 3 次
        assert message_table._model.rowCount() == 3, f"过滤后应有3行(0x1A0)，实际 {message_table._model.rowCount()}"

        texts = _child_texts(message_table._model, 0)
        assert "EngineRPM" in texts, f"过滤后展开应包含 EngineRPM，实际: {texts}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
