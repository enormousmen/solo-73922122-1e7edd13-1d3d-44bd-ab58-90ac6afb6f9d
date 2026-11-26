# 内网授时源探测 API

更换内网授时源前，用本服务并发探测两个候选 NTP 时源：**各比本机快/慢多少**、
**网络往返要多久**。探测进行中可随时拨动模拟本机墙钟，报告按“出报告时”的
墙钟基准统一换算两路时差。

## 已实现功能

- **并发探测**：`POST /probe` 并发向两路本地 NTP 时源各发**一次** UDP 请求
  （真实 SNTP 48 字节报文，VN=4），两路都结束后出报告。
- **时差换算**：每路 `offset_seconds` = 时源钟 − 本机墙钟（正=时源快，负=慢），
  按标准公式 `((t2−t1)+(t3−t4))/2` 计算；t1/t4 以单调钟记录，出报告时统一
  换算到**当时的墙钟基准**——拨钟前已收到的有效样本照样计入并随新基准平移。
- **往返延迟**：`rtt_seconds = (t4−t1) − (t3−t2)`，即扣除时源处理时间
  （处理时间 `processing_seconds` 单列）；用单调钟度量，**不随拨钟改变**。
- **超时**：每路发出后 **1 秒**（`PROBE_TIMEOUT_SECONDS` 可改）未收到**匹配**
  （origin 时间戳一致）的回包即标 `timeout`；非匹配回包丢弃继续等；
  超时用单调钟判定，**不随拨钟改变**。
- **模拟本机墙钟**：`POST /clock/adjust` 步进拨钟（正快负慢），`GET /clock`
  查看当前墙钟与累计拨钟偏移；拨钟只改墙钟基准，不影响超时与延迟测量。
- **两个本地模拟 NTP 时源**（`app/mock_server.py`）：时钟偏移
  （`SERVER_OFFSET_SECONDS`）与**回包快慢**（`SERVER_DELAY_SECONDS`）均可设。
- **Docker Compose 一键启动**：`api` + `ntp-a` + `ntp-b` 三个服务。
- **演示脚本** `demo.sh`：在 A、B 两路回包之间拨快墙钟 1 小时并取报告。

## 目录结构

```
app/
  main.py         FastAPI 入口与 API 路由
  probe.py        探测客户端：并发两路、匹配回包、1s 超时、出报告
  clock.py        模拟本机墙钟（单调钟 + 可拨偏移）
  ntp.py          最小 SNTP 报文编解码（48 字节，1900 纪元 64 位时间戳）
  mock_server.py  本地模拟 NTP 时源（偏移、回包延迟可用环境变量设置）
  settings.py     运行配置（环境变量）
  Dockerfile
docker-compose.yml
demo.sh           演示：两路回包之间拨钟
tests/            pytest 自测（13 个用例）
```

## 快速开始

### Docker Compose（推荐）

```bash
docker compose up --build -d
./demo.sh                 # 演示：两路回包之间拨钟
curl -X POST localhost:8000/probe | python3 -m json.tool --no-ensure-ascii
```

Compose 中两路时源配置（`docker-compose.yml`，可改）：

| 服务   | 时钟偏移 | 回包延迟 |
|--------|----------|----------|
| ntp-a  | +2.5s（比真实快） | 0.15s |
| ntp-b  | −1.2s（比真实慢） | 0.7s  |

### 本地（无 Docker）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
SERVER_PORT=12300 SERVER_OFFSET_SECONDS=2.5 SERVER_DELAY_SECONDS=0.15 .venv/bin/python -m app.mock_server &
SERVER_PORT=12301 SERVER_OFFSET_SECONDS=-1.2 SERVER_DELAY_SECONDS=0.7 .venv/bin/python -m app.mock_server &
NTP_A_PORT=12300 NTP_B_PORT=12301 .venv/bin/uvicorn app.main:app --port 8000 &
./demo.sh
```

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/probe` | 并发探测两路时源，两路结束后返回报告 |
| POST | `/clock/adjust` | 拨动模拟墙钟，body：`{"delta_seconds": 3600}` |
| GET  | `/clock` | 当前模拟墙钟 / 累计拨钟偏移 / 拨钟次数 |
| GET  | `/servers` | 两路候选时源配置与超时时间 |
| GET  | `/health` | 健康检查 |

报告字段（每路）：`status`（ok/timeout/error）、`offset_seconds`（时源−本机，
按出报告时墙钟基准）、`rtt_seconds`（扣除时源处理时间的网络往返）、
`processing_seconds`（时源处理时间 t3−t2）、`timestamps`（t1..t4，本机侧
已换算到出报告时基准）、`clock_adjustments_after_receive`（>0 表示该样本
是拨钟前收到的，仍有效）。

## 实测结果（本环境真实运行）

### 1) 两路回包之间拨钟（`./demo.sh`，A 0.15s 回、B 0.7s 回，0.35s 时拨 +3600s）

```json
{
  "probe_started_wall": 1795658858.798815,
  "report_wall_time": 1795662459.500989,
  "local_clock_offset_seconds": 3600.0,
  "clock_adjustments_total": 1,
  "timeout_seconds": 1.0,
  "paths": [
    {
      "name": "source-a", "server": "127.0.0.1:12300", "status": "ok",
      "offset_seconds": -3597.499951,
      "rtt_seconds": 0.000346, "processing_seconds": 0.150605,
      "clock_adjustments_after_receive": 1
    },
    {
      "name": "source-b", "server": "127.0.0.1:12301", "status": "ok",
      "offset_seconds": -3601.199975,
      "rtt_seconds": 0.000282, "processing_seconds": 0.70103,
      "clock_adjustments_after_receive": 0
    }
  ]
}
```

解读：

- A 实际快 2.5s、B 实际慢 1.2s；拨快本机 1 小时后出报告，两路时差按新基准
  换算为 `2.5−3600=−3597.5`、`−1.2−3600=−3601.2`，与实测一致。
- A 的样本是**拨钟前**收到的（`clock_adjustments_after_receive=1`），照样计入。
- `rtt_seconds`≈0.3ms：0.15s/0.7s 的时源处理时间已被扣除（见 `processing_seconds`）。

### 2) 超时（B 回包 1.5s > 1s 超时线）

```
POST /probe  →  A: status=ok, offset=+2.500026, rtt=0.000272
                B: status=timeout
整个请求耗时 1.022s（两路并发，由 1s 超时一路决定）
```

### 3) 拨钟不影响超时与延迟（B 0.9s 回包，探测中猛拨 +10000s）

```
POST /probe（后台） → 0.3s 时 POST /clock/adjust +10000 → 等两路结束
A: status=ok, offset=-9997.499964, rtt=0.000281
B: status=ok, offset=-10001.200017, rtt=0.000253   ← 未被误判超时
```

若超时用墙钟判定，+10000s 会让 B 立刻“超时”；实测两路均 ok，rtt 与未拨钟时一致。

### 4) 自测

```
$ .venv/bin/python -m pytest
13 passed in 5.03s
```

覆盖：报文编解码往返、墙钟拨动与历史时刻换算、两路偏移/RTT/处理时间、
超时判定、两路回包之间拨钟的基准换算、前向大拨钟不误判超时且 RTT 不变、
非匹配回包丢弃后判超时、HTTP 层（含探测中调 `/clock/adjust`）全流程。

## 设计要点

- **双钟分离**：墙钟 = 单调钟 + 基准 + 累计拨钟偏移。报文时间戳、报告展示用
  墙钟；超时与 RTT 用单调钟。拨钟只改偏移，因此延迟与超时天然不受影响。
- **出报告时基准换算**：t1/t4 存单调钟，报告时统一 `wall = mono + 当前基准`，
  所以拨钟前后的样本都能用、且都反映“现在”的墙钟。
- **回包匹配**：应答中的 origin 时间戳必须等于本次发出的 t1，否则丢弃继续等，
  直到 1s 单调钟截止。

## 未完成项 / 限制

- **Docker Compose 配置未在本环境实测**（本环境无 docker 命令）；以上实测均为
  本地进程等效运行（同一套代码、同一 demo.sh）。compose/Dockerfile 仅经静态核对。
- 每次探测只取**单次样本**，未做多次采样、滤波或最优样本选择（真实 ntpd 会多次采样）。
- 时差公式假设往返路径对称；未校验 LI/stratum/Kiss-o'-Death 等 NTP 告警字段。
- 拨钟为步进（step）模拟，未模拟 slew（渐进调整）与钟漂。
- 报告不持久化，仅返回当次结果；无并发探测互斥（同时多个 /probe 各自独立，
  但共享同一个模拟墙钟）。
- API 无认证鉴权（内网探测场景从简）。
