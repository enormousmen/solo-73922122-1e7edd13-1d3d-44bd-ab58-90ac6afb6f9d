"""探测逻辑自测：偏移换算、超时、拨钟对延迟/超时的影响。

模拟时源直接在测试进程内用 asyncio 起在 127.0.0.1 随机端口上。
"""
import asyncio
import time

import pytest

from app.clock import WallClock
from app.mock_server import MockNtpServer
from app.ntp import build_response
from app.probe import run_probe


async def _start_server(offset: float, delay: float):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: MockNtpServer(offset, delay), local_addr=("127.0.0.1", 0)
    )
    port = transport.get_extra_info("sockname")[1]
    return transport, port


def test_both_paths_ok_offsets_and_rtt():
    async def main():
        clock = WallClock()
        ta, pa = await _start_server(offset=2.5, delay=0.05)
        tb, pb = await _start_server(offset=-1.2, delay=0.10)
        try:
            return await run_probe(
                [("source-a", "127.0.0.1", pa), ("source-b", "127.0.0.1", pb)],
                clock,
                timeout=1.0,
            )
        finally:
            ta.close()
            tb.close()

    report = asyncio.run(main())
    a, b = report["paths"]
    assert a["status"] == "ok" and b["status"] == "ok"
    # 时差 = 时源偏移 - 本机拨钟偏移（未拨钟为 0）
    assert a["offset_seconds"] == pytest.approx(2.5, abs=0.05)
    assert b["offset_seconds"] == pytest.approx(-1.2, abs=0.05)
    # 往返延迟已扣除时源处理时间：远小于处理时间本身
    assert 0 <= a["rtt_seconds"] < 0.05
    assert a["processing_seconds"] == pytest.approx(0.05, abs=0.03)
    assert b["processing_seconds"] == pytest.approx(0.10, abs=0.03)


def test_timeout_when_reply_too_slow():
    async def main():
        clock = WallClock()
        ta, pa = await _start_server(offset=0.0, delay=1.5)  # 超过 1s -> 超时
        tb, pb = await _start_server(offset=1.0, delay=0.05)
        start = time.monotonic()
        try:
            report = await run_probe(
                [("source-a", "127.0.0.1", pa), ("source-b", "127.0.0.1", pb)],
                clock,
                timeout=1.0,
            )
        finally:
            ta.close()
            tb.close()
        return report, time.monotonic() - start

    report, elapsed = asyncio.run(main())
    a, b = report["paths"]
    assert a["status"] == "timeout"
    assert "offset_seconds" not in a
    assert b["status"] == "ok"
    # 两路并发：总耗时由最慢一路（1s 超时）决定，远小于串行的 1s+0.05s 再排队
    assert elapsed == pytest.approx(1.0, abs=0.3)


def test_adjust_between_replies_uses_report_time_baseline():
    """A 回包后、B 回包前拨钟：两路样本都按出报告时的新基准换算。"""

    async def main():
        clock = WallClock()
        ta, pa = await _start_server(offset=2.5, delay=0.05)  # A 先回
        tb, pb = await _start_server(offset=-1.2, delay=0.40)  # B 后回

        async def adjust_later():
            await asyncio.sleep(0.20)  # 落在 A、B 回包之间
            clock.adjust(500.0)

        task = asyncio.create_task(adjust_later())
        try:
            report = await run_probe(
                [("source-a", "127.0.0.1", pa), ("source-b", "127.0.0.1", pb)],
                clock,
                timeout=1.0,
            )
        finally:
            ta.close()
            tb.close()
        await task
        return report

    report = asyncio.run(main())
    a, b = report["paths"]
    assert a["status"] == "ok" and b["status"] == "ok"
    # 出报告时墙钟已拨快 500s：两路时差都按新基准换算
    assert a["offset_seconds"] == pytest.approx(2.5 - 500, abs=0.05)
    assert b["offset_seconds"] == pytest.approx(-1.2 - 500, abs=0.05)
    # 拨钟前收到的 A 样本照样计入
    assert a["clock_adjustments_after_receive"] == 1
    assert b["clock_adjustments_after_receive"] == 0
    assert report["local_clock_offset_seconds"] == 500.0


def test_rtt_and_timeout_not_affected_by_clock_jump():
    """前向拨大钟：不误判超时，往返延迟不变（单调钟度量）。"""

    async def run_once(jump: bool):
        clock = WallClock()
        ta, pa = await _start_server(offset=0.0, delay=0.05)
        tb, pb = await _start_server(offset=0.0, delay=0.60)
        task = None
        if jump:

            async def jumper():
                await asyncio.sleep(0.10)
                clock.adjust(10000.0)  # 若超时用墙钟，B 会被立刻误判超时

            task = asyncio.create_task(jumper())
        try:
            report = await run_probe(
                [("source-a", "127.0.0.1", pa), ("source-b", "127.0.0.1", pb)],
                clock,
                timeout=1.0,
            )
        finally:
            ta.close()
            tb.close()
        if task is not None:
            await task
        return report

    async def main():
        plain = await run_once(False)
        jumped = await run_once(True)
        return plain, jumped

    plain, jumped = asyncio.run(main())
    assert jumped["paths"][0]["status"] == "ok"
    assert jumped["paths"][1]["status"] == "ok"  # 拨 +10000s 不触发超时
    for i in (0, 1):
        rtt_plain = plain["paths"][i]["rtt_seconds"]
        rtt_jumped = jumped["paths"][i]["rtt_seconds"]
        assert abs(rtt_plain - rtt_jumped) < 0.05


def test_unmatched_reply_is_ignored_until_timeout():
    """origin 不匹配的回包被丢弃，等到 1s 判超时。"""

    class WrongOriginServer(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            now = time.time()
            # 回一个 origin 不匹配的应答
            self.transport.sendto(build_response(12345, now, now), addr)

    async def main():
        clock = WallClock()
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            WrongOriginServer, local_addr=("127.0.0.1", 0)
        )
        port = transport.get_extra_info("sockname")[1]
        try:
            return await run_probe(
                [("source-a", "127.0.0.1", port)], clock, timeout=1.0
            )
        finally:
            transport.close()

    report = asyncio.run(main())
    assert report["paths"][0]["status"] == "timeout"
