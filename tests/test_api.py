"""API 层自测：完整走 HTTP 接口。

模拟时源跑在独立守护线程的事件循环里（真实 UDP 回包），
包含关键场景：探测进行中、两路回包之间通过 API 拨钟。
"""
import asyncio
import threading

import httpx
import pytest

from app.mock_server import MockNtpServer

OFFSET_A = 2.5
OFFSET_B = -1.2


class _ServerThread:
    """在守护线程里跑一个模拟 NTP 时源，返回实际监听的 UDP 端口。"""

    def __init__(self, offset: float, delay: float) -> None:
        self.offset = offset
        self.delay = delay
        self.port: int | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> int:
        self._thread.start()
        assert self._ready.wait(timeout=5), "mock server 未能启动"
        assert self.port is not None
        return self.port

    def _run(self) -> None:
        async def amain() -> None:
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: MockNtpServer(self.offset, self.delay),
                local_addr=("127.0.0.1", 0),
            )
            self.port = transport.get_extra_info("sockname")[1]
            self._ready.set()
            await asyncio.Event().wait()  # 守护线程随进程退出

        asyncio.run(amain())


@pytest.fixture(scope="module")
def mock_servers():
    a = _ServerThread(OFFSET_A, 0.05).start()  # A 回包快
    b = _ServerThread(OFFSET_B, 0.40).start()  # B 回包慢
    return {"a": a, "b": b}


@pytest.fixture()
def api(mock_servers, monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "ntp_a_host", "127.0.0.1")
    monkeypatch.setattr(main.settings, "ntp_a_port", mock_servers["a"])
    monkeypatch.setattr(main.settings, "ntp_b_host", "127.0.0.1")
    monkeypatch.setattr(main.settings, "ntp_b_port", mock_servers["b"])
    return main


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def test_health_and_servers(api, mock_servers):
    async def main():
        async with _client(api.app) as client:
            r = await client.get("/health")
            assert r.status_code == 200 and r.json()["status"] == "ok"
            r = await client.get("/servers")
            targets = {t["name"]: t for t in r.json()["targets"]}
            assert targets["source-a"]["port"] == mock_servers["a"]
            assert targets["source-b"]["port"] == mock_servers["b"]
            assert r.json()["timeout_seconds"] == 1.0

    asyncio.run(main())


def test_clock_adjust_validation(api):
    async def main():
        async with _client(api.app) as client:
            r = await client.post("/clock/adjust", json={"delta_seconds": "abc"})
            assert r.status_code == 422
            before = (await client.get("/clock")).json()["offset_seconds"]
            r = await client.post("/clock/adjust", json={"delta_seconds": 10.0})
            assert r.status_code == 200
            assert r.json()["offset_seconds"] == pytest.approx(before + 10.0)
            # 拨回，避免影响同进程其他用例
            await client.post("/clock/adjust", json={"delta_seconds": -10.0})

    asyncio.run(main())


def test_probe_api_both_ok(api):
    async def main():
        async with _client(api.app) as client:
            clock_before = (await client.get("/clock")).json()
            r = await client.post("/probe")
            assert r.status_code == 200
            return clock_before, r.json()

    clock_before, report = asyncio.run(main())
    a, b = report["paths"]
    local_offset = clock_before["offset_seconds"]
    assert a["status"] == "ok" and b["status"] == "ok"
    assert a["offset_seconds"] == pytest.approx(OFFSET_A - local_offset, abs=0.1)
    assert b["offset_seconds"] == pytest.approx(OFFSET_B - local_offset, abs=0.1)
    assert a["rtt_seconds"] >= 0


def test_probe_api_adjust_between_replies(api):
    """HTTP 层完整复现场景：A 回包后、B 回包前调用 /clock/adjust。"""

    async def main():
        async with _client(api.app) as client:
            clock_before = (await client.get("/clock")).json()

            async def adjust_later():
                await asyncio.sleep(0.15)  # A(0.05s) 已回、B(0.40s) 未回
                await client.post("/clock/adjust", json={"delta_seconds": 500.0})

            task = asyncio.create_task(adjust_later())
            r = await client.post("/probe")
            await task
            assert r.status_code == 200
            return clock_before, r.json()

    clock_before, report = asyncio.run(main())
    a, b = report["paths"]
    base = clock_before["offset_seconds"] + 500.0  # 出报告时的本机拨钟偏移
    assert a["status"] == "ok" and b["status"] == "ok"
    # 两路时差都按拨钟后的新基准换算
    assert a["offset_seconds"] == pytest.approx(OFFSET_A - base, abs=0.1)
    assert b["offset_seconds"] == pytest.approx(OFFSET_B - base, abs=0.1)
    # 拨钟前收到的 A 样本照样计入
    assert a["clock_adjustments_after_receive"] == 1
    assert b["clock_adjustments_after_receive"] == 0
    assert report["local_clock_offset_seconds"] == pytest.approx(base)
