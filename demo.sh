#!/usr/bin/env bash
# 演示：在“两路回包之间”拨动模拟本机墙钟。
# 时序：A 约 0.15s 回包 -> 0.35s 时拨快 1 小时 -> B 约 0.7s 回包 -> 出报告。
# 用法：先启动服务（docker compose up --build，或见 README 本地起法），再执行 ./demo.sh
set -euo pipefail

BASE="${BASE_URL:-http://127.0.0.1:8000}"
RESULT="$(mktemp)"
trap 'rm -f "$RESULT"' EXIT

pp() { python3 -m json.tool --no-ensure-ascii; }

echo "== 0) 两路候选时源配置 =="
curl -s "$BASE/servers" | pp

echo
echo "== 1) 探测前的模拟本机墙钟 =="
curl -s "$BASE/clock" | pp

echo
echo "== 2) 后台发起探测（并发两路，各发一次，1s 超时） =="
curl -s -X POST "$BASE/probe" > "$RESULT" &
PROBE_PID=$!

sleep 0.35
echo "== 3) 此时 A 已回包、B 未回包：拨快本机墙钟 +3600s =="
curl -s -X POST "$BASE/clock/adjust" \
  -H 'Content-Type: application/json' \
  -d '{"delta_seconds": 3600}' | pp

echo
echo "== 4) 等两路结束，取报告（按出报告时的墙钟基准换算） =="
wait "$PROBE_PID"
cat "$RESULT" | pp

echo
echo "解读：A(+2.5s)/B(-1.2s) 两个时源的 offset 都按拨钟后的新基准换算，"
echo "各自约减少 3600s；拨钟前收到的 A 样本照样计入"
echo "（clock_adjustments_after_receive=1）；rtt 与超时不受拨钟影响。"
