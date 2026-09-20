# tests/test_multi_select_filter_search.py
"""MultiSelectIdFilter 顶部搜索框行为测试。

覆盖：搜索过滤（含 0x 前缀 / 小写归一化）、无匹配提示、搜索不改变勾选状态、
回车提交唯一命中项、菜单展开自动重置搜索、候选 ID 重建后搜索词保持，以及
报文表格页「搜索 -> 勾选 -> 即时过滤」的端到端链路。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from widgets.multi_select_filter import MultiSelectIdFilter

_IDS = [0x1A0, 0x1A1, 0x2C0, 0x123]

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
def flt(app):
    w = MultiSelectIdFilter()
    w.set_ids(list(_IDS))
    yield w
    w.deleteLater()


@pytest.fixture
def shown(flt):
    """弹出菜单，让列表拿到真实几何（点击测试需要 visualRect 有效）。"""
    flt._menu.popup(flt.mapToGlobal(flt.rect().bottomLeft()))
    QApplication.processEvents()
    QApplication.processEvents()
    yield flt
    flt._menu.close()


def _click_row(flt, row: int, x: int):
    """在某行的 x 位置点一下（真实鼠标按下 + 抬起），返回该 item。"""
    lst = flt._list
    item = lst.item(row)
    rect = lst.visualRect(lst.indexFromItem(item))
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier,
                     QPoint(x, rect.center().y()))
    QApplication.processEvents()
    return item


def _visible(flt):
    """当前可见项：None 代表「全部」快捷项。"""
    out = []
    for i in range(flt._list.count()):
        item = flt._list.item(i)
        if item.isHidden():
            continue
        data = item.data(Qt.UserRole)
        out.append(None if data is None else int(data))
    return out


def _type(flt, text):
    """模拟用户在搜索框输入（走真实按键事件）。"""
    flt._search.clear()
    QTest.keyClicks(flt._search, text)


class TestSearchFilter:
    def test_initial_all_visible(self, flt):
        assert _visible(flt) == [None] + _IDS

    def test_filter_by_hex_fragment(self, flt):
        _type(flt, "A0")
        assert _visible(flt) == [0x1A0], "搜索时「全部」项应让位给具体 ID"

    def test_prefix_and_lowercase_normalized(self, flt):
        _type(flt, "0x2c0")
        assert _visible(flt) == [0x2C0]

    def test_no_match_shows_hint(self, flt):
        _type(flt, "ZZ")
        assert _visible(flt) == []
        assert flt._empty_action.isVisible(), "无匹配时应弹出提示"

    def test_clear_restores_all(self, flt):
        _type(flt, "ZZ")
        flt._search.clear()
        assert _visible(flt) == [None] + _IDS
        assert not flt._empty_action.isVisible()

    def test_search_keeps_selection(self, flt):
        flt.set_selected_ids([0x1A0])
        _type(flt, "2C0")
        assert flt.selected_ids() == [0x1A0], "搜索只隐藏，不应改变勾选"
        assert _visible(flt) == [0x2C0]


class TestSearchSubmit:
    def test_enter_toggles_single_match(self, flt):
        _type(flt, "0x2C0")
        QTest.keyClick(flt._search, Qt.Key_Return)
        assert flt.selected_ids() == [0x2C0]
        QTest.keyClick(flt._search, Qt.Key_Return)
        assert flt.selected_ids() == []

    def test_enter_emits_selection_changed(self, flt):
        seen = []
        flt.selectionChanged.connect(lambda: seen.append(1))
        _type(flt, "0x2C0")
        QTest.keyClick(flt._search, Qt.Key_Return)
        assert len(seen) == 1

    def test_enter_with_multiple_matches_does_nothing(self, flt):
        _type(flt, "1")
        assert len(_visible(flt)) > 1
        QTest.keyClick(flt._search, Qt.Key_Return)
        assert flt.selected_ids() == []


class TestMenuAndRefill:
    def test_about_to_show_resets_search(self, flt):
        _type(flt, "A0")
        flt._on_menu_about_to_show()
        assert flt._search.text() == ""
        assert _visible(flt) == [None] + _IDS

    def test_set_ids_keeps_query(self, app):
        w = MultiSelectIdFilter()
        w.set_ids(list(_IDS))
        _type(w, "A0")
        # 实时报文页场景：候选 ID 持续累积，重建列表后搜索词仍应生效
        w.set_ids(list(_IDS) + [0x3A0])
        assert _visible(w) == [0x1A0, 0x3A0]
        w.deleteLater()


class TestRowClickToggle:
    """点复选框本身也要能勾选（回归：曾经必须点行的其他位置才行）。

    旧实现用 itemPressed 回调记录状态来判断「Qt 是否已自行切换复选框」，但点击落在
    复选框判定区内时 Qt 走 delegate 的 editorEvent、`pressed` 信号根本不发出，
    回调里拿到的是上一次交互的残留值：残留值若恰好等于切换后的状态，补偿逻辑就会
    误判为「没有切换」再补一次，把刚勾上的又切回去。
    """

    def test_checkbox_click_after_all_row_click(self, shown):
        flt = shown
        _click_row(flt, 0, 150)          # 点「全部」行中部，制造残留 press 状态
        item = flt._list.item(1)         # 0x1A0
        assert item.checkState() == Qt.Unchecked
        _click_row(flt, 1, 15)           # 点该行复选框
        assert item.checkState() == Qt.Checked, "点复选框应能勾选"

    @pytest.mark.parametrize("x", [4, 10, 15, 24, 150])
    def test_click_anywhere_toggles(self, shown, x):
        flt = shown
        _click_row(flt, 0, 150)          # 先做一次整行点击
        item = _click_row(flt, 1, x)
        assert item.checkState() == Qt.Checked, "点 x=%d 应勾选" % x
        _click_row(flt, 1, x)
        assert item.checkState() == Qt.Unchecked, "再点 x=%d 应取消" % x

    def test_other_row_checkbox_after_dirty_state(self, shown):
        """已勾选行被再次点击后（残留状态=已勾选），另一行的复选框仍要能勾上。"""
        flt = shown
        _click_row(flt, 1, 150)          # 0x1A0 勾上
        assert flt._list.item(1).checkState() == Qt.Checked
        _click_row(flt, 1, 150)          # 再点一次取消，残留 press 状态=已勾选
        item = _click_row(flt, 2, 15)    # 点 0x1A1 的复选框
        assert item.checkState() == Qt.Checked, "0x1A1 应被勾选"
        assert flt._list.item(1).checkState() == Qt.Unchecked
        assert flt.selected_ids() == [0x1A1]

    def test_all_row_still_restores_when_unchecked(self, shown):
        """「全部」行被点击取消勾选时，仍应自动恢复（不允许空筛选）。"""
        flt = shown
        item = _click_row(flt, 0, 150)
        assert item.checkState() == Qt.Checked
        assert flt.selected_ids() == []


class TestMessageTableSearchToFilter:
    def test_search_then_check_filters_rows(self, app):
        import numpy as np
        import pandas as pd
        from widgets.message_table import MessageTableWidget

        frame_index = pd.DataFrame({
            "frame_id": np.array([0, 1, 2, 3, 4], dtype=np.int64),
            "timestamp": np.array([0.0, 0.001, 0.002, 0.003, 0.004], dtype=np.float64),
            "arbitration_id": np.array([0x1A0, 0x1A1, 0x1A0, 0x1A1, 0x1A0], dtype=np.uint32),
            "dlc": np.array([8, 8, 8, 8, 8], dtype=np.uint8),
            "channel": np.array([0, 0, 0, 0, 0], dtype=np.int32),
            "is_fd": np.array([False, False, False, False, False], dtype=bool),
        })
        raw = np.zeros((5, 8), dtype=np.uint8)

        table = MessageTableWidget()
        table.set_data(frame_index, raw, [], "")
        assert table._model.rowCount() == 5

        id_filter = table._id_filter
        ids = [id_filter._list.item(i).data(Qt.UserRole)
               for i in range(id_filter._list.count())
               if not id_filter._list.item(i).isHidden()]
        assert ids == [None, 0x1A0, 0x1A1], "候选 ID 应按升序注入"

        # 搜到 0x1A1 后整行勾选（不依赖小方块命中）-> 表格立即按 ID 过滤
        _type(id_filter, "1A1")
        row = [i for i in range(id_filter._list.count())
               if id_filter._list.item(i).data(Qt.UserRole) == 0x1A1][0]
        id_filter._list.item(row).setCheckState(Qt.Checked)
        assert table._model.rowCount() == 2, "0x1A1 出现 2 次"

        # 继续搜索并追加勾选，形成多 ID 过滤
        id_filter._search.clear()
        _type(id_filter, "1A0")
        row0 = [i for i in range(id_filter._list.count())
                if id_filter._list.item(i).data(Qt.UserRole) == 0x1A0][0]
        id_filter._list.item(row0).setCheckState(Qt.Checked)
        assert table._model.rowCount() == 5, "两个 ID 合计 5 帧"
        assert id_filter.selected_ids() == [0x1A0, 0x1A1]

        table._reset_filter()
        assert table._model.rowCount() == 5
        assert id_filter.selected_ids() == []

        table.deleteLater()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
