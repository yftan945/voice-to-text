#!/usr/bin/env bash
# 示範伺服器：不需要 GPU，用「虛構」的示範資料啟動完整的網頁（README 截圖用）。
#
# 用法：
#   scripts/demo.sh          建置前端、產生示範音檔、在 Docker 裡塞資料並啟動（背景執行）
#   DEMO_LANG=en scripts/demo.sh   同上，但用英文示範資料（一次只跑一種語言，會取代正在跑的容器）
#   scripts/demo.sh stop     停止並移除容器
#   scripts/demo.sh logs     看容器 log
#
# 網址：http://127.0.0.1:8899（只綁本機）。容器內不啟動 worker（create_app(start_worker=False)），
# 處理中與排隊中的任務狀態不會變。資料放在容器裡，停止後就消失；每次啟動重新建立。
#
# 環境變數：
#   DEMO_LANG=zh-TW           示範資料的語言：zh-TW（預設）或 en
#   DEMO_PORT=8899            本機埠號
#   DEMO_DIR=/tmp/v2t-demo    暫存目錄（示範音檔、pip 快取），不在 repo 裡
#   DEMO_SKIP_BUILD=1         略過 npm run build（已經建置過時）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="voice-to-text-demo"
PORT="${DEMO_PORT:-8899}"
WORK="${DEMO_DIR:-/tmp/v2t-demo}"
IMAGE="python:3.12-slim"
LANG_CODE="${DEMO_LANG:-zh-TW}"
case "$LANG_CODE" in
  zh-TW|en) ;;
  *) echo "DEMO_LANG 只支援 zh-TW 或 en（目前是 $LANG_CODE）" >&2; exit 2 ;;
esac

case "${1:-start}" in
  stop)
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "已停止 $NAME" || echo "$NAME 沒有在執行"
    exit 0 ;;
  logs)
    exec docker logs -f "$NAME" ;;
  start) ;;
  *) echo "用法：$0 [start|stop|logs]" >&2; exit 2 ;;
esac

command -v docker >/dev/null || { echo "需要 Docker" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "需要 ffmpeg（macOS：brew install ffmpeg），用來產生示範音檔" >&2; exit 1; }

if [ "${DEMO_SKIP_BUILD:-0}" != "1" ]; then
  echo "==> 建置前端"
  (cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
fi
[ -f "$ROOT/frontend/dist/index.html" ] || { echo "找不到 frontend/dist，請先建置前端" >&2; exit 1; }

echo "==> 產生示範音檔（$WORK/audio）"
mkdir -p "$WORK/audio" "$WORK/pip-cache"
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/scripts/demo_seed.py" --audio-only --audio-cache "$WORK/audio" --lang "$LANG_CODE"

docker rm -f "$NAME" >/dev/null 2>&1 || true
echo "==> 啟動容器（第一次需要下載映像與安裝套件，約一兩分鐘）"
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8899" \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONUNBUFFERED=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -e DEMO_LANG="$LANG_CODE" -e DATA_DIR=/data -e STATIC_DIR=/repo/frontend/dist \
  -v "$ROOT/backend:/repo/backend:ro" \
  -v "$ROOT/scripts:/repo/scripts:ro" \
  -v "$ROOT/frontend/dist:/repo/frontend/dist:ro" \
  -v "$WORK/audio:/audio:ro" \
  -v "$WORK/pip-cache:/root/.cache/pip" \
  -w /repo/backend "$IMAGE" sh -c '
    set -e
    pip install -q -r requirements-dev.txt
    python /repo/scripts/demo_seed.py --data-dir /data --audio-cache /audio --lang "$DEMO_LANG"
    exec python -c "
import uvicorn
from app.main import create_app
uvicorn.run(create_app(start_worker=False), host=\"0.0.0.0\", port=8899, log_level=\"warning\")
"
  ' >/dev/null

echo -n "==> 等待服務就緒"
for _ in $(seq 1 120); do
  if curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo
    echo "完成：http://127.0.0.1:$PORT"
    echo "停止：scripts/demo.sh stop"
    exit 0
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != "true" ]; then
    echo; echo "容器已結束，log：" >&2; docker logs "$NAME" >&2; exit 1
  fi
  echo -n "."; sleep 2
done
echo; echo "等待逾時，看 log：scripts/demo.sh logs" >&2; exit 1
