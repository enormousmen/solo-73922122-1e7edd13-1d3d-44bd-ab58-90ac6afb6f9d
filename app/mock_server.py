"""本地模拟 NTP 时源（被探测对象）。

环境变量：
  SERVER_OFFSET_SECONDS  时源时钟相对真实时间的偏移（正=快），默认 0
  SERVER_DELAY_SECONDS   收到请求后延迟多久回包（时源处理时间），默认 0.1
  SERVER_HOST            监听地址，默认 0.0.0.0
  SERVER_PORT            监听 UDP 端口，默认 123
"""
from __future__ import annotations

import asyncio
import os
import time

from .ntp import build_response, parse_request_origin


class MockNtpServer(asyncio.DatagramProtocol):
    def __init__(self, offset_seconds: float, delay_seconds: float) -> None:
        self.offset = offset_seconds
        self.delay = delay_seconds
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            origin = parse_request_origin(data)
        except ValueError:
            return
        t2 = time.time() + self.offset  # 时源本地钟：收到时刻
        asyncio.get_running_loop().create_task(self._reply(addr, origin, t2))

    async def _reply(self, addr, origin: int, t2: float) -> None:
        await asyncio.sleep(self.delay)  # 模拟时源处理耗时（回包快慢）
        t3 = time.time() + self.offset  # 时源本地钟：发出时刻
        assert self.transport is not None
        self.transport.sendto(build_response(origin, t2, t3), addr)


async def _main() -> None:
    host = os.environ.get("SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SERVER_PORT", "123"))
    offset = float(os.environ.get("SERVER_OFFSET_SECONDS", "0"))
    delay = float(os.environ.get("SERVER_DELAY_SECONDS", "0.1"))
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: MockNtpServer(offset, delay), local_addr=(host, port)
    )
    print(
        f"mock NTP server on {host}:{port} "
        f"offset={offset:+}s delay={delay}s",
        flush=True,
    )
    await asyncio.Event().wait()  # 常驻


if __name__ == "__main__":
    asyncio.run(_main())
