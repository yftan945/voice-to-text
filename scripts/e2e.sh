#!/usr/bin/env bash
# 端對端驗證：上傳 → 追 SSE 進度 → 取結果 → 匯出 SRT
# 用法：scripts/e2e.sh <音檔> [base_url] [額外 curl -F 參數…]
set -euo pipefail
FILE="$1"; BASE="${2:-${VTT_URL:-http://127.0.0.1:8810}}"; shift; shift || true

if [ -n "${E2E_JOB_ID:-}" ]; then
  ID="$E2E_JOB_ID"   # 接續追蹤既有任務
else
  echo "==> 上傳 $FILE"
  JOB=$(curl -fsS -F "file=@$FILE" -F diarize=true "$@" "$BASE/api/jobs")
  ID=$(printf '%s' "$JOB" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
fi
echo "job id: $ID"

echo "==> 進度（SSE）"
curl -fsSN "$BASE/api/jobs/$ID/events" | python3 -u -c '
import json, sys
event = None
for line in sys.stdin:
    line = line.rstrip("\n")
    if line.startswith("event: "):
        event = line[7:]
    elif line.startswith("data: ") and event == "job":
        j = json.loads(line[6:])
        print("  %-8s %5.1f%%  %s" % (j["status"], j["progress"] * 100, j["stage"]), flush=True)
        if j["status"] in ("done", "failed", "canceled"):
            if j.get("error"): print("  錯誤：", j["error"])
            break
'

echo "==> 任務"
curl -fsS "$BASE/api/jobs/$ID" | python3 -m json.tool | head -40
echo "==> 逐字稿摘要"
curl -fsS "$BASE/api/jobs/$ID/transcript" | python3 -c '
import json, sys
t = json.load(sys.stdin)["current"]
print("diarization:", t["diarization"])
print("speakers:", {k: v.get("name") for k, v in t["speakers"].items()})
print("warnings:", t["warnings"])
print("segments:", len(t["segments"]))
for s in t["segments"][:8]:
    print("  [%7.2f] %s: %s" % (s["start"], s["speaker"], s["text"]))
'
echo "==> SRT（前 20 行）"
curl -fsS "$BASE/api/jobs/$ID/export/srt" | head -20
