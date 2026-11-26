"""NTP 探测客户端。

每路时源一个独立 UDP 套接字、各发一次请求；
超时与往返延迟一律用单调钟度量 —— 拨动墙钟不会改变它们。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .clock import WallClock
from .ntp import build_request, parse_response


@dataclass
class PathResult:
    name: str
    host: str
    port: int
    status: str  # "ok" | "timeout" | "error"
    t1_mono: float = 0.0
    t4_mono: float | None = None
    t2: float | None = None
    t3: float | None = None
    adjustments_at_receive: int = 0
    error: str | None = None


class _ClientProtocol(asyncio.DatagramProtocol):
    """收到数据立刻打单调钟时间戳入队，保证 t4 尽量贴近真实到达时刻。"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait((time.monotonic(), data))


async def probe_path(
    name: str, host: str, port: int, clock: WallClock, timeout: float
) -> PathResult:
    """向单个时源发一次 NTP 请求；timeout 秒内无匹配回包则标超时。"""
    result = PathResult(name=name, host=host, port=port, status="error")
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            _ClientProtocol, remote_addr=(host, port)
        )
    except OSError as exc:  # 地址不可达等
        result.error = f"socket: {exc}"
        return result

    try:
        # 发送时刻：单调钟计时 + 同一时刻的墙钟写入报文
        t1_mono = clock.mono_now()
        t1_wall = clock.wall_from_mono(t1_mono)
        packet, t1_ntp = build_request(t1_wall)
        result.t1_mono = t1_mono
        transport.sendto(packet)

        deadline = t1_mono + timeout  # 单调钟截止点：拨钟不影响超时
        while True:
            remaining = deadline - clock.mono_now()
            if remaining <= 0:
                result.status = "timeout"
                return result
            try:
                t4_mono, data = await asyncio.wait_for(
                    protocol.queue.get(), remaining
                )
            except asyncio.TimeoutError:
                result.status = "timeout"
                return result
            try:
                resp = parse_response(data)
            except ValueError:
                continue  # 非合法应答：丢弃，继续等匹配回包
            if resp["origin"] != t1_ntp:
                continue  # 不是本次请求的回包：丢弃，继续等
            result.status = "ok"
            result.t4_mono = t4_mono
            result.t2 = resp["t2"]
            result.t3 = resp["t3"]
            result.adjustments_at_receive = clock.adjustments
            return result
    finally:
        transport.close()


async def run_probe(
    targets: list[tuple[str, str, int]],
    clock: WallClock,
    timeout: float,
) -> dict:
    """并发探测全部时源；两路都结束后，按“出报告时”的墙钟基准出报告。"""
    started = clock.snapshot()
    results = await asyncio.gather(
        *(probe_path(name, host, port, clock, timeout) for name, host, port in targets)
    )

    # 两路均已结束 —— 此刻的墙钟基准用于换算所有样本
    report_clock = clock.snapshot()
    paths = []
    for r in results:
        path: dict = {
            "name": r.name,
            "server": f"{r.host}:{r.port}",
            "status": r.status,
        }
        if r.status == "ok":
            # 关键：t1/t4 以单调钟记录，这里统一换算到出报告时的墙钟基准。
            # 拨钟前收到的样本照样计入，只是随新基准整体平移。
            t1_wall = clock.wall_from_mono(r.t1_mono)
            t4_wall = clock.wall_from_mono(r.t4_mono)
            offset = ((r.t2 - t1_wall) + (r.t3 - t4_wall)) / 2.0
            rtt = (r.t4_mono - r.t1_mono) - (r.t3 - r.t2)  # 扣除时源处理时间
            processing = r.t3 - r.t2
            path.update(
                {
                    "offset_seconds": round(offset, 6),
                    "rtt_seconds": round(max(rtt, 0.0), 6),
                    "processing_seconds": round(processing, 6),
                    "timestamps": {
                        "t1_send_wall": round(t1_wall, 6),
                        "t2_server_recv": round(r.t2, 6),
                        "t3_server_send": round(r.t3, 6),
                        "t4_receive_wall": round(t4_wall, 6),
                    },
                    # >0 表示回包收到之后又发生过拨钟（样本是拨钟前收到的，仍有效）
                    "clock_adjustments_after_receive": report_clock["adjustments"]
                    - r.adjustments_at_receive,
                }
            )
        elif r.status == "error":
            path["error"] = r.error
        paths.append(path)

    return {
        "probe_started_wall": round(started["wall_now"], 6),
        "report_wall_time": round(report_clock["wall_now"], 6),
        "local_clock_offset_seconds": report_clock["offset_seconds"],
        "clock_adjustments_total": report_clock["adjustments"],
        "timeout_seconds": timeout,
        "interpretation": (
            "offset_seconds>0 表示该时源比本机墙钟快，<0 表示慢；"
            "rtt_seconds 为扣除时源处理时间后的网络往返延迟"
        ),
        "paths": paths,
    }
