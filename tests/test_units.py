"""NTP 报文编解码 与 模拟墙钟 的单元测试。"""
import time

import pytest

from app.clock import WallClock
from app.ntp import (
    build_request,
    build_response,
    from_ntp,
    parse_request_origin,
    parse_response,
    to_ntp,
)


def test_ntp_timestamp_roundtrip():
    now = time.time()
    assert from_ntp(to_ntp(now)) == pytest.approx(now, abs=1e-6)


def test_request_response_flow():
    t1 = time.time()
    pkt, t1_ntp = build_request(t1)
    assert parse_request_origin(pkt) == t1_ntp

    t2 = t1 + 0.01
    t3 = t2 + 0.05
    resp = build_response(t1_ntp, t2, t3)
    parsed = parse_response(resp)
    assert parsed["origin"] == t1_ntp
    assert parsed["t2"] == pytest.approx(t2, abs=1e-6)
    assert parsed["t3"] == pytest.approx(t3, abs=1e-6)


def test_parse_response_rejects_garbage():
    with pytest.raises(ValueError):
        parse_response(b"\x00" * 10)
    req, _ = build_request(time.time())
    with pytest.raises(ValueError):  # 客户端报文(mode=3)不是合法应答
        parse_response(req)


def test_wall_clock_adjust_and_convert():
    clock = WallClock()
    mono_before = clock.mono_now()
    wall_before = clock.wall_now()
    clock.adjust(3600.0)
    # 墙钟随拨钟跳动
    assert clock.wall_now() == pytest.approx(wall_before + 3600.0, abs=0.05)
    # 单调钟不受拨钟影响
    assert clock.mono_now() - mono_before < 1.0
    # 拨钟前的历史时刻按出报告时的新基准换算：整体平移 3600s
    converted = clock.wall_from_mono(mono_before)
    assert converted == pytest.approx(wall_before + 3600.0, abs=0.05)
