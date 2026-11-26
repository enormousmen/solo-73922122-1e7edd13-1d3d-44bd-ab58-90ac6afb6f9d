"""运行配置：均可用环境变量覆盖。"""
from __future__ import annotations

import os


class Settings:
    def __init__(self) -> None:
        self.ntp_a_host = os.environ.get("NTP_A_HOST", "127.0.0.1")
        self.ntp_a_port = int(os.environ.get("NTP_A_PORT", "12300"))
        self.ntp_b_host = os.environ.get("NTP_B_HOST", "127.0.0.1")
        self.ntp_b_port = int(os.environ.get("NTP_B_PORT", "12301"))
        # 每路发出后多少秒未收到匹配回包判超时（需求：1 秒）
        self.probe_timeout = float(os.environ.get("PROBE_TIMEOUT_SECONDS", "1.0"))

    def targets(self) -> list[tuple[str, str, int]]:
        return [
            ("source-a", self.ntp_a_host, self.ntp_a_port),
            ("source-b", self.ntp_b_host, self.ntp_b_port),
        ]
