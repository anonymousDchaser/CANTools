# core/can_connection.py
"""进程内共享的 CAN 设备连接管理器（单例式，由主窗口持有并注入各页）。

为什么需要它：
- PEAK 同一物理通道在【同一进程内只能 Initialize 一次】。若模拟上报页、
  实时监控页、实时报文页各自 `can.Bus(...)`，第二个 Initialize 会返回
  “A PCAN Channel has not been initialized yet”，导致后开的页用不了设备。
- 因此三页共用【唯一一条】总线：连接/初始化只做一次；发送与接收都走它。
- 多个接收者（监控页、报文页）同时收帧会被「瓜分」（每帧只被一个 recv 取
  走），所以用唯一收帧线程把每帧 fan-out 给所有监听者，保证两页都看到全部帧。
- 用户忘了在「连接状态」页点连接，各页点击动作时通过 ensure_connected
  自动连接设备再执行相应动作。
"""
from PyQt5.QtCore import QObject, pyqtSignal, QThread

import can

from core.can_utils import (
    connect_bus, DEFAULT_CHANNEL, DEFAULT_BITRATE, DEFAULT_INTERFACE_TYPE,
    DEVICE_TYPES,
)


class _CanReadDispatcher(QThread):
    """唯一收帧线程：持有共享总线循环 recv，把每帧同步分发给所有监听回调。

    注意：多个监听回调（监控页/报文页的 process_message）在本线程内被依次
    调用，因此每帧都会被【所有】监听者收到，而不是被瓜分。
    """

    def __init__(self, bus, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._running = False
        self._cbs = []  # 监听回调 list[callable(can.Message)]

    def set_callbacks(self, cbs):
        # 传入的是 manager._listeners 的引用，后续增删会直接反映到此列表
        self._cbs = cbs

    def run(self):
        self._running = True
        while self._running:
            try:
                msg = self._bus.recv(timeout=0.05)
            except Exception:  # noqa: BLE001 — 总线被主线程关闭（断开）或异常
                break
            if msg is None:
                continue
            # 快照遍历，避免与 add/remove_listener 的并发增删冲突
            for cb in list(self._cbs):
                try:
                    cb(msg)
                except Exception:  # noqa: BLE001 — 单个监听者异常不应拖垮收帧
                    pass
        self._running = False


class CanConnectionManager(QObject):
    """进程内共享 CAN 连接管理器。"""

    # (connected: bool, info_text: str)
    state_changed = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus = None
        self._config = None  # {"interface_type","channel","bitrate"}
        self._dispatcher = None
        self._listeners = []  # list[callable(can.Message)]

    # ── 状态查询 ──
    @property
    def is_connected(self):
        return self._bus is not None

    def get_bus(self):
        return self._bus

    def get_config(self) -> dict | None:
        """返回当前连接配置（只读副本），供功能页自动连接时对齐参数。"""
        return dict(self._config) if self._config is not None else None

    def config_matches(self, interface_type, channel, bitrate) -> bool:
        if self._config is None:
            return False
        return (self._config["interface_type"] == interface_type and
                self._config["channel"] == channel and
                self._config["bitrate"] == bitrate)

    # ── 连接 / 断开 ──
    def connect(self, interface_type, channel, bitrate):
        """建立或复用共享总线。返回 (bus, None) 成功；(None, err) 失败。

        幂等：已连接且配置一致时直接返回已有总线；配置不一致且仍有监听者
        占用时拒绝（避免把正在用的设备踢掉）。
        """
        if self._bus is not None:
            if self.config_matches(interface_type, channel, bitrate):
                return self._bus, None
            return None, (
                f"设备已以不同配置连接（当前: "
                f"{self._config['interface_type']} / {self._config['channel']} @ "
                f"{self._config['bitrate']}）。\n"
                f"请先在「连接状态」页断开，再修改配置后重新连接。"
            )
        bus, err = connect_bus(interface_type, channel, bitrate)
        if bus is None:
            return None, err
        self._bus = bus
        self._config = {"interface_type": interface_type,
                        "channel": channel, "bitrate": bitrate}
        label = DEVICE_TYPES.get(interface_type, {}).get("label", interface_type)
        self.state_changed.emit(True, f"{label} / {channel} @ {bitrate}")
        return bus, None

    def ensure_connected(self, interface_type, channel, bitrate):
        """功能页自动连接用：未连则连接，已连则复用。返回 (bus, None)/(None, err)。"""
        return self.connect(interface_type, channel, bitrate)

    def dispatch(self, msg):
        """把本进程内「发出」的帧（如模拟上报发出的帧）主动 fan-out 给所有监听者。

        为什么需要它：硬件（如 PCAN）默认【不】把自身发出的帧回环到收帧线程，
        因此模拟上报页 bus.send() 上线的帧不会被 _CanReadDispatcher 收到，监控页
        /报文页也就看不到自己模拟的信号。这里在发送侧主动把帧喂给所有监听者，
        与 dispatcher 收帧线程（负责外部下发的帧）互补，二者来源不同、互不重复。
        """
        if not self._listeners:
            return
        for cb in list(self._listeners):
            try:
                cb(msg)
            except Exception:  # noqa: BLE001 — 单个监听者异常不应拖垮发送
                pass

    def disconnect(self):
        """显式断开共享总线（由「连接状态」页「断开 CAN」触发）。"""
        self._stop_dispatcher()
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
            self._config = None
            self.state_changed.emit(False, "")

    # ── 收帧监听（监控页 / 报文页）──
    def add_listener(self, cb) -> object:
        """注册一个收帧监听回调（callable，接收 can.Message）。返回当前总线或 None。"""
        if self._bus is None:
            return None
        if cb not in self._listeners:
            self._listeners.append(cb)
        self._ensure_dispatcher()
        return self._bus

    def remove_listener(self, cb):
        """注销收帧监听回调；无监听者时停止收帧线程。"""
        if cb in self._listeners:
            self._listeners.remove(cb)
        if not self._listeners:
            self._stop_dispatcher()

    def _ensure_dispatcher(self):
        if self._dispatcher is None and self._bus is not None:
            self._dispatcher = _CanReadDispatcher(self._bus)
            self._dispatcher.set_callbacks(self._listeners)
            self._dispatcher.start()

    def _stop_dispatcher(self):
        if self._dispatcher is not None:
            self._dispatcher._running = False
            self._dispatcher.wait(2000)
            self._dispatcher = None
