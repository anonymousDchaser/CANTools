"""列表/树条目的自定义数据存取。

历史说明
--------
Qt6 迁移期间曾出现「加载分组配置后主窗口 show() 直接崩溃、打包版进度条走完
没有画面」的故障，一度被误判为 `setData(UserRole, ...)` 存放 Python 对象导致
的 QVariant 缺陷，于是改成模块级 `id(item)` 外置映射。

**该归因是错的。** 真实原因是
`widgets/drag_reorder_list.py` 的 `_draw_handle()` 里
`painter.drawEllipse(QPoint(...), 1.6, 1.6)` 在 PyQt6 下没有匹配重载：
`QPoint` 只能配 `int` 半径、`float` 半径必须配 `QPointF`。该行只在
「条目带 payload（据此判定可拖拽）」时才执行，且抛出的是 Python 异常，
发生在 `paint()` 这个虚函数里，PyQt6 无法把它交回 Python 侧，最终
`abort()`（退出码 `0xC0000409`）。所以现象正好是「有分组数据必崩、无数据正常」。

因此这里恢复 Qt 原生存储（`QVariant` 完全可用），只是把角色集中定义，
避免散落的魔法数字：

- 树条目 `QTreeWidgetItem.setData(column, role, value)`
- 列表条目 `QListWidgetItem.setData(role, value)`

两套签名不同，故按类型分流。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidgetItem

# 统一的自定义角色（与测试中直接读写的 `UserRole + 200` 保持一致）
PAYLOAD_ROLE = int(Qt.ItemDataRole.UserRole) + 200
EYE_ROLE = int(Qt.ItemDataRole.UserRole) + 201


def _set(item, role, value):
    if isinstance(item, QTreeWidgetItem):
        item.setData(0, role, value)
    else:
        item.setData(role, value)


def _get(item, role):
    if isinstance(item, QTreeWidgetItem):
        return item.data(0, role)
    return item.data(role)


def set_payload(item, value):
    """为条目附带任意 Python 数据。"""
    if item is None:
        return
    _set(item, PAYLOAD_ROLE, value)


def get_payload(item, default=None):
    """读取条目附带的数据。"""
    if item is None:
        return default
    value = _get(item, PAYLOAD_ROLE)
    return default if value is None else value


def has_payload(item):
    """条目是否附带数据（等价于原 `data(role) is not None` 判定）。"""
    return get_payload(item) is not None


def set_eye_visible(item, visible):
    """记录眼睛图标状态。"""
    if item is None:
        return
    _set(item, EYE_ROLE, bool(visible))


def get_eye_visible(item):
    """眼睛图标状态，默认 True（显示曲线）。"""
    if item is None:
        return True
    value = _get(item, EYE_ROLE)
    return True if value is None else bool(value)


def has_eye_state(item):
    """该条目是否被显式设置过眼睛状态。"""
    if item is None:
        return False
    return _get(item, EYE_ROLE) is not None
