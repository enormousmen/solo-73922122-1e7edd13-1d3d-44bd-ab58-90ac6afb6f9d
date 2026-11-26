"""最小 NTP(SNTP) 报文编解码。

48 字节定长报文；时间戳为 1900 纪元的 64 位定点数（秒<<32 | 小数）。
客户端 t1 -> 时源收到记 t2 -> 时源发出记 t3 -> 客户端收到记 t4。
"""
from __future__ import annotations

import struct

NTP_EPOCH_OFFSET = 2_208_988_800  # 1900-01-01 -> 1970-01-01 的秒数
MODE_CLIENT = 3
MODE_SERVER = 4
VERSION = 4

# flags(1) stratum(1) poll(1) precision(1) root_delay(4) root_dispersion(4)
# ref_id(4) ref_ts(8) origin_ts(8) recv_ts(8) xmit_ts(8)
_PACKET = struct.Struct("!BBbbII4sQQQQ")
PACKET_SIZE = _PACKET.size  # 48


def to_ntp(ts_unix: float) -> int:
    """Unix 秒 -> NTP 64 位时间戳。"""
    secs = int(ts_unix) + NTP_EPOCH_OFFSET
    frac = int((ts_unix - int(ts_unix)) * (1 << 32)) & 0xFFFFFFFF
    return (secs << 32) | frac


def from_ntp(ts: int) -> float:
    """NTP 64 位时间戳 -> Unix 秒。"""
    return (ts >> 32) - NTP_EPOCH_OFFSET + (ts & 0xFFFFFFFF) / float(1 << 32)


def build_request(t1_unix: float) -> tuple[bytes, int]:
    """构造客户端请求。

    返回 (报文字节, t1 的 NTP 整数形式)；后者用于校验回包是否匹配本次请求。
    """
    t1 = to_ntp(t1_unix)
    packet = _PACKET.pack(
        (VERSION << 3) | MODE_CLIENT,  # LI=0, VN=4, Mode=3(client)
        0, 0, 0, 0, 0,
        b"\x00" * 4,
        0,  # reference timestamp
        0,  # origin timestamp
        0,  # receive timestamp
        t1,  # transmit timestamp
    )
    return packet, t1


def parse_request_origin(data: bytes) -> int:
    """取出客户端请求里的发送时间戳（应答时作为 origin 原样带回）。"""
    if len(data) < PACKET_SIZE:
        raise ValueError("packet too short")
    return _PACKET.unpack(data[:PACKET_SIZE])[10]  # transmit timestamp


def build_response(origin: int, t2_unix: float, t3_unix: float) -> bytes:
    """构造时源应答：origin 原样回显，t2=收到时刻，t3=发出时刻（时源本地钟）。"""
    return _PACKET.pack(
        (VERSION << 3) | MODE_SERVER,  # LI=0, VN=4, Mode=4(server)
        1, 6, -20,
        0, 0,
        b"LOCL",
        to_ntp(t2_unix),  # reference timestamp（此处复用 t2）
        origin,
        to_ntp(t2_unix),  # receive timestamp
        to_ntp(t3_unix),  # transmit timestamp
    )


def parse_response(data: bytes) -> dict:
    """解析时源应答；非合法应答抛 ValueError。"""
    if len(data) < PACKET_SIZE:
        raise ValueError("packet too short")
    fields = _PACKET.unpack(data[:PACKET_SIZE])
    mode = fields[0] & 0x07
    if mode != MODE_SERVER:
        raise ValueError(f"not a server response (mode={mode})")
    return {
        "stratum": fields[1],
        "origin": fields[8],
        "t2": from_ntp(fields[9]),
        "t3": from_ntp(fields[10]),
    }
