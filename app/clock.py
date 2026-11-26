"""模拟本机墙钟。

墙钟 = 单调钟 + 基准 + 累计拨钟偏移：

- ``wall_now()`` 随拨钟跳动，用于写入 NTP 报文、展示报告时刻；
- ``mono_now()`` 永远单调递增，用于超时判断与往返延迟测量，拨钟不影响它；
- 拨钟只是改偏移量，因此任何历史单调时间戳都可以按“出报告时”的
  最新基准换算回墙钟（``wall_from_mono``），拨钟前收到的样本照样有效。
"""
from __future__ import annotations

import threading
import time


class WallClock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 单调钟 -> 真实墙钟的固定基准（启动时对齐一次）
        self._real_base = time.time() - time.monotonic()
        self._offset = 0.0  # 累计拨钟偏移（秒），正=拨快
        self._adjustments = 0  # 拨钟次数

    def mono_now(self) -> float:
        """单调钟：测超时、测往返延迟专用，永不回拨。"""
        return time.monotonic()

    def wall_now(self) -> float:
        """当前墙钟（含累计拨钟偏移）。"""
        with self._lock:
            return time.monotonic() + self._real_base + self._offset

    def wall_from_mono(self, mono_ts: float) -> float:
        """把历史单调时间戳换算到“当前（出报告时）”的墙钟基准。"""
        with self._lock:
            return mono_ts + self._real_base + self._offset

    def adjust(self, delta_seconds: float) -> None:
        """拨动墙钟（步进 delta 秒，正=拨快，负=拨慢）。"""
        with self._lock:
            self._offset += delta_seconds
            self._adjustments += 1

    def snapshot(self) -> dict:
        with self._lock:
            mono = time.monotonic()
            return {
                "wall_now": mono + self._real_base + self._offset,
                "mono_now": mono,
                "offset_seconds": self._offset,
                "adjustments": self._adjustments,
            }

    @property
    def adjustments(self) -> int:
        with self._lock:
            return self._adjustments
