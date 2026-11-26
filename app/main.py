"""内网授时源探测 API。

- POST /probe         并发向两路本地 NTP 时源各发一次请求，两路结束后出报告
- POST /clock/adjust  拨动模拟本机墙钟（探测进行中也可调用）
- GET  /clock         查看当前模拟墙钟
- GET  /servers       查看两路候选时源配置
"""
from __future__ import annotations

import json
import math

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .clock import WallClock
from .probe import run_probe
from .settings import Settings


class UTF8JSONResponse(JSONResponse):
    """报告含中文说明，输出不转义非 ASCII 字符。"""

    def render(self, content) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            indent=None, separators=(",", ":"),
        ).encode("utf-8")


app = FastAPI(
    title="内网授时源探测 API",
    summary="并发探测两路本地 NTP 时源：各比本机快/慢多少、网络往返多久",
    version="1.0.0",
    default_response_class=UTF8JSONResponse,
)
clock = WallClock()
settings = Settings()


class AdjustRequest(BaseModel):
    delta_seconds: float = Field(..., description="拨钟量（秒），正=拨快，负=拨慢")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/clock")
async def get_clock() -> dict:
    """当前模拟本机墙钟及其相对真实时间的偏移。"""
    return clock.snapshot()


@app.post("/clock/adjust")
async def adjust_clock(req: AdjustRequest) -> dict:
    """拨动模拟本机墙钟（步进）。只改墙钟基准，不影响超时与延迟测量。"""
    if not math.isfinite(req.delta_seconds):
        raise HTTPException(status_code=400, detail="delta_seconds 必须是有限数值")
    clock.adjust(req.delta_seconds)
    return clock.snapshot()


@app.get("/servers")
async def servers() -> dict:
    """当前配置的两路候选时源与超时时间。"""
    return {
        "targets": [
            {"name": name, "host": host, "port": port}
            for name, host, port in settings.targets()
        ],
        "timeout_seconds": settings.probe_timeout,
    }


@app.post("/probe")
async def probe() -> dict:
    """并发探测两路时源；等两路都结束（收到匹配回包或超时）后出报告。"""
    return await run_probe(settings.targets(), clock, settings.probe_timeout)
