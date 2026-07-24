# workers/can_sim_worker.py
"""信号模拟上报后台 worker：通过 PCAN 周期性发送选中信号的 CAN 报文

设计要点：
- 独立 QThread 中创建并使用独立的 can.Bus（发送），不跨线程共享总线。
- 按 frame_id 将选中信号聚合成一帧，每周期编码并发送一次。
- 支持「自动递增」模式：对指定信号按步长在 [min,max] 内循环变化，
  用于在没有真实 ECU 时产生动态报文流。
- 运行中可被 GUI 线程通过 set_values 修改静态值；共享字典用锁保护，
  避免与发送循环产生竞态（满足持锁/跨线程安全自审要求）。
"""
import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal

import can

from core.can_utils import (
    connect_bus, load_dbc, build_signal_slots, group_by_frame, encode_frame,
)


class CanSimWorker(QThread):
    """周期性编码并发送选中信号报文的后台线程"""

    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    frame_sent = pyqtSignal(int, str)  # (frame_id, data_hex)

    def __init__(self, dbc_path: str, signals: list,
                 values: dict, ramp_config: dict,
                 period: float = 1.0,
                 channel: str = "PCAN_USBBUS1", bitrate: int = 500000):
        super().__init__()
        self._dbc_path = dbc_path
        self._signals = signals
        self._channel = channel
        self._bitrate = bitrate
        self._period = max(period, 0.01)
        # 静态值：{(msg_name, sig_name): float}
        self._values = dict(values)
        # 自动递增配置：{(msg_name, sig_name): (min, max, step)}
        self._ramp = dict(ramp_config)
        self._running = False
        self._lock = threading.Lock()

    def run(self):
        self._running = True
        bus, err = connect_bus(self._channel, self._bitrate)
        if bus is None:
            self.error_occurred.emit(err)
            return
        try:
            db, err = load_dbc(self._dbc_path)
            if db is None:
                self.error_occurred.emit(err)
                return
            slots, err = build_signal_slots(db, self._signals)
            if slots is None:
                self.error_occurred.emit(err)
                return
            groups = group_by_frame(slots)
            if not groups:
                self.error_occurred.emit("没有可发送的信号")
                return

            # 自动递增的当前值基线（从静态值初始化，循环外只初始化一次）
            with self._lock:
                cur = {k: float(v) for k, v in self._values.items()}
            ramp_cur = dict(cur)  # 递增状态在循环间持续累积，不能每周期重置

            self.status_changed.emit(
                f"已连接 PCAN，模拟上报 {len(self._signals)} 个信号，"
                f"周期 {self._period:.2f}s"
            )

            while self._running:
                # 锁内拷贝静态值（非递增信号可实时更新）；递增状态 ramp_cur 在循环间持久累积
                with self._lock:
                    static_vals = dict(self._values)
                for group in groups:
                    vals = {}
                    for slot in group.signals:
                        k = (slot.msg_name, slot.sig_name)
                        if k in self._ramp:
                            vals[slot.sig_name] = ramp_cur.get(k, 0.0)
                        else:
                            vals[slot.sig_name] = static_vals.get(k, 0.0)
                    data, enc_err = encode_frame(db, group, vals)
                    if enc_err:
                        self.error_occurred.emit(enc_err)
                        continue
                    is_extended = group.frame_id > 0x7FF
                    try:
                        bus.send(can.Message(
                            arbitration_id=group.frame_id,
                            data=data,
                            is_extended_id=is_extended,
                        ))
                        self.frame_sent.emit(
                            group.frame_id,
                            " ".join(f"{b:02X}" for b in data),
                        )
                    except Exception as e:  # noqa: BLE001
                        self.error_occurred.emit(f"发送失败: {e}")
                        break

                # 更新自动递增值（锁内修改）
                if self._ramp:
                    with self._lock:
                        for k, (mn, mx, step) in self._ramp.items():
                            nxt = ramp_cur.get(k, mn) + step
                            if nxt > mx:
                                nxt = mn
                            ramp_cur[k] = nxt
                time.sleep(self._period)
        except Exception as e:  # noqa: BLE001
            self.error_occurred.emit(f"模拟上报异常: {e}")
        finally:
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self.status_changed.emit("模拟上报已停止")

    def stop(self):
        """请求停止发送"""
        self._running = False

    def set_values(self, values: dict):
        """运行中更新静态值（由 GUI 线程调用，锁保护）"""
        with self._lock:
            self._values.update(values)
