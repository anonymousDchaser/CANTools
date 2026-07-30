"""键盘 Delete 键删除选中项的事件过滤器。

用于「已选信号列表 / 分组信号列表」等控件：用户选中条目后直接按 Delete 键
移除，等价于点击「移除选中」按钮。
"""
from PyQt5.QtCore import QObject, QEvent, Qt


class DelKeyFilter(QObject):
    """将目标控件上按下的 Delete 键转发为回调。

    - 仅拦截发往 target 控件自身的 KeyPress 事件（其子控件如备注列的
      QLineEdit 内的按键不会被本过滤器截获，避免编辑备注时误删信号行）；
    - 将自身 parent 设为 target，使其随 target 生命周期存活，避免被 GC 回收。
    """

    def __init__(self, target, on_delete):
        super().__init__(target)
        self._on_delete = on_delete
        target.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Delete:
            self._on_delete()
            return True
        return super().eventFilter(obj, event)
