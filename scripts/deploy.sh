#!/usr/bin/env bash
# 部署到 GPU 主機：同步程式碼 → 在 GPU 主機上 docker compose up -d --build
#
# 同步用 rsync --delete：本機刪掉的程式檔，GPU 主機上也會刪掉（不然舊檔會留在映像裡）。
# 只同步下面 SOURCES 列的檔案與資料夾；GPU 主機上的 data/、models/、.env、bench/、bench-data/ 一律保護，不會被刪或覆蓋。
# 每次都先預覽（rsync -n --itemize-changes）：有檔案要被刪除時列出清單，輸入 y 才繼續；
# 一次最多刪 300 個檔案（--max-delete），超過就停下來（通常代表路徑設錯）。
#
# 用法：
#   scripts/deploy.sh              # 預覽 → 同步＋建置＋啟動
#   scripts/deploy.sh --dry-run    # 只顯示預覽（要更新、要刪除哪些檔案），不同步、不建置
#   scripts/deploy.sh --sync-only  # 預覽 → 只同步程式碼
#   scripts/deploy.sh --base-only  # 預覽 → 同步 → 只預先建置 Python 推論環境那一層（第一次部署可先跑，最久）
#   加 --force（或 DEPLOY_FORCE=1）可略過「線上還有排隊中／轉錄中任務」的檢查
#
# 部署前檢查：先查線上服務 /api/jobs，只要有 queued／running 的任務就列出並中止（結束代碼 1），
# 不同步、不重建（重建會重啟容器，轉錄到一半的任務會被打斷）。--dry-run 也會檢查並顯示，但不中止預覽。
# 連不上服務（容器沒起來）時只警告並繼續，不然服務壞掉時就無法部署修正。
#
# 環境變數：
#   DEPLOY_HOST  GPU 主機的 ssh 主機名稱（預設 gpu-host）
#   DEPLOY_DIR   GPU 主機上的部署目錄，相對於家目錄，只接受 docker/ 底下的路徑（預設 docker/voice-to-text）
#   DEPLOY_YES=1 有檔案要刪除時不詢問，直接繼續（先用 --dry-run 看過清單再用）；不會略過任務檢查
#   DEPLOY_FORCE=1 線上有任務在跑也照樣部署（印警告）；等同 --force
#   DEPLOY_URL   線上服務網址，用來查進行中的任務（預設 http://127.0.0.1:8810）
#
# 本機的 rsync 是 macOS 的 openrsync（protocol 29），GPU 主機上是 rsync 3.x；這裡只用兩邊都支援的選項。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 本機設定（不進 git）：DEPLOY_HOST、DEPLOY_URL、OP_ITEM、OP_VAULT 等，範例見 .deploy.env.example
[ -f "$ROOT/.deploy.env" ] && . "$ROOT/.deploy.env"
HOST="${DEPLOY_HOST:-gpu-host}"
REMOTE_DIR="${DEPLOY_DIR:-docker/voice-to-text}"   # 相對於 GPU 主機的家目錄
SERVICE_URL="${DEPLOY_URL:-http://127.0.0.1:8810}"
MODE=""
FORCE="${DEPLOY_FORCE:-}"

usage() {
  sed -n '9,15p' "$0" | sed 's/^# \{0,1\}//' >&2
}

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --dry-run|--sync-only|--base-only)
      if [ -n "$MODE" ] && [ "$MODE" != "$arg" ]; then
        echo "!! $MODE 與 $arg 不能同時使用" >&2; usage; exit 2
      fi
      MODE="$arg" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "!! 不認得的參數：$arg" >&2; usage; exit 2 ;;
  esac
done

# rsync --delete 的目標只能在 docker/ 底下：擋掉家目錄本身、絕對路徑、..、其他地方
REMOTE_DIR="${REMOTE_DIR%/}"
if [[ ! "$REMOTE_DIR" =~ ^docker/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ ]] \
  || [[ "/$REMOTE_DIR/" == *"/../"* || "/$REMOTE_DIR/" == *"/./"* ]]; then
  echo "!! DEPLOY_DIR 只能是 docker/ 底下的路徑（例如 docker/voice-to-text），收到：${DEPLOY_DIR:-}" >&2
  exit 2
fi

# 要同步的來源（相對於 repo 根目錄）；其他東西（docs、design-demos、bench…）不會送到 GPU 主機
SOURCES=(Dockerfile docker-compose.yml .dockerignore .env.example README.md backend frontend scripts)

RSYNC_OPTS=(
  -az --delete --max-delete=300
  # 保護 GPU 主機上的資料：P＝不會被 --delete 刪掉（開頭的 / 代表部署目錄的最上層）
  "--filter=P /data/" "--filter=P /models/" "--filter=P /.env"
  "--filter=P /bench/" "--filter=P /bench-data/"
  # 不送的東西（被排除的檔案在 GPU 主機上也不會被刪）
  --exclude=.git/ --exclude=node_modules/ --exclude=dist/ --exclude=__pycache__/
  --exclude=.pytest_cache/ --exclude=.DS_Store "--exclude=*.tsbuildinfo" --exclude=.env
)

# ssh 的連接埠轉送警告（~/.ssh/config 設了 LocalForward）不顯示
quiet_ssh() { grep -v -e '8189' -e 'local forwarding' >&2 || true; }
ssh_q() { ssh "$HOST" "$@" 2> >(quiet_ssh); }

cd "$ROOT"
for s in "${SOURCES[@]}"; do
  [ -e "$s" ] || { echo "!! 找不到要同步的來源：$s" >&2; exit 1; }
done

# 部署前檢查：線上有排隊中／轉錄中的任務就不能重建（會重啟容器，打斷進行中的轉錄）。
# 回傳 0＝沒有進行中的任務或無法檢查（連不上只警告）；回傳 1＝有進行中的任務（已列出）。
check_active_jobs() {
  local url="${SERVICE_URL%/}/api/jobs" body rc=0 out prc=0
  echo "==> 檢查線上進行中的任務：$url"
  body="$(curl -sf --connect-timeout 3 --max-time 10 "$url")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "!! 警告：連不上線上服務（curl 結束代碼 ${rc}），無法確認有沒有進行中的任務。" >&2
    echo "!!       可能是容器沒起來；為了能部署修正，仍然繼續。若服務其實正常，請確認 DEPLOY_URL 是否正確。" >&2
    return 0
  fi
  # /api/jobs 回傳任務陣列；每行輸出：檔名、狀態、階段、進度（0~1 轉百分比）。結束代碼 10＝有進行中的任務、11＝格式不符
  out="$(printf '%s' "$body" | python3 -I -c '
import json, sys
try:
    data = json.load(sys.stdin)
    if isinstance(data, dict):
        data = data.get("jobs")
    if not isinstance(data, list):
        raise ValueError("not a list")
    active = [j for j in data if j.get("status") in ("queued", "running")]
except Exception:
    sys.exit(11)
for j in active:
    try:
        pct = "%d%%" % round(float(j.get("progress") or 0) * 100)
    except (TypeError, ValueError):
        pct = "?"
    print("%s\t%s\t%s\t%s" % (j.get("filename") or j.get("id") or "?", j.get("status"), j.get("stage") or "-", pct))
sys.exit(10 if active else 0)
')" || prc=$?
  case "$prc" in
    0) echo "    沒有排隊中或轉錄中的任務"; return 0 ;;
    10) ;;
    *)
      echo "!! 警告：服務回應的格式看不懂，無法確認有沒有進行中的任務，仍然繼續。" >&2
      return 0 ;;
  esac
  echo "--- 進行中的任務（檔名 / 狀態 / 階段 / 進度）"
  printf '%s\n' "$out" | awk -F'\t' '{ printf "    %s / %s / %s / %s\n", $1, $2, $3, $4 }'
  return 1
}

if ! check_active_jobs; then
  if [ "$MODE" = "--dry-run" ]; then
    echo "==> （--dry-run）線上有進行中的任務；只預覽，不中止。實際部署會在這裡停下來"
  elif [ "$FORCE" = "1" ]; then
    echo "!! 警告：線上有進行中的任務，但已指定 --force／DEPLOY_FORCE=1，照樣部署。重建會重啟容器，上面的任務會被中斷（重啟後會重新排隊）。" >&2
  else
    echo "!! 線上還有進行中的任務，不同步、不重建。等任務跑完再部署；真的要部署就加 --force（或 DEPLOY_FORCE=1）。DEPLOY_YES=1 不會略過這項檢查。" >&2
    exit 1
  fi
fi

echo "==> 預覽同步到 $HOST:~/$REMOTE_DIR"
rc=0
preview="$(rsync "${RSYNC_OPTS[@]}" -n --itemize-changes "${SOURCES[@]}" "$HOST:$REMOTE_DIR/" 2> >(quiet_ssh))" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "!! 預覽失敗（rsync 結束代碼 ${rc}）。要刪除的檔案超過 300 個時也會停在這裡，請先確認 DEPLOY_DIR 是否正確。" >&2
  exit 1
fi

# openrsync 輸出「*deleting path」，rsync 3 是「*deleting   path」
deletions="$(printf '%s\n' "$preview" | sed -n -E 's/^\*deleting +//p')"
updates="$(printf '%s\n' "$preview" | grep -v -E '^\*deleting' | grep -v -E '^$' || true)"
n_del=0
[ -n "$deletions" ] && n_del="$(printf '%s\n' "$deletions" | wc -l | tr -d ' ')"
n_upd=0
[ -n "$updates" ] && n_upd="$(printf '%s\n' "$updates" | wc -l | tr -d ' ')"
echo "    要更新：$n_upd 項　要刪除：$n_del 項"

if [ "$MODE" = "--dry-run" ]; then
  if [ -n "$updates" ]; then
    echo "--- 要更新（新增或內容有變）"
    printf '%s\n' "$updates" | sed 's/^/    /'
  fi
fi
if [ -n "$deletions" ]; then
  echo "--- 要刪除（GPU 主機上有、本機已經沒有）"
  printf '%s\n' "$deletions" | sed 's/^/    /'
fi

if [ "$MODE" = "--dry-run" ]; then
  echo "==> 只預覽（--dry-run），沒有同步任何檔案"
  exit 0
fi

if [ -n "$deletions" ] && [ "${DEPLOY_YES:-}" != "1" ]; then
  if [ ! -t 0 ]; then
    echo "!! 有 $n_del 個檔案要刪除，需要確認。沒有可以輸入的終端機：看過上面的清單後用 DEPLOY_YES=1 重跑。" >&2
    exit 1
  fi
  read -r -p "確定要刪除上面 $n_del 個檔案並繼續部署嗎？輸入 y 繼續：" ans || ans=""
  if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
    echo "==> 已取消，沒有同步任何檔案"
    exit 1
  fi
fi

echo "==> 同步程式碼到 $HOST:~/$REMOTE_DIR"
ssh_q "mkdir -p ~/$REMOTE_DIR/data ~/$REMOTE_DIR/models"
rsync "${RSYNC_OPTS[@]}" "${SOURCES[@]}" "$HOST:$REMOTE_DIR/" 2> >(quiet_ssh)

case "$MODE" in
  --sync-only)
    exit 0 ;;
  --base-only)
    echo "==> 建置 Python 推論環境（base stage）"
    ssh_q "cd ~/$REMOTE_DIR && DOCKER_BUILDKIT=1 docker build --target base -t voice-to-text-base:dev ."
    exit 0 ;;
esac

if ! ssh_q "test -f ~/$REMOTE_DIR/.env"; then
  echo "!! GPU 主機上沒有 .env（HF_TOKEN）。講者辨識需要它，請先執行 scripts/set-hf-token.sh" >&2
fi

echo "==> 建置並啟動"
ssh_q "cd ~/$REMOTE_DIR && docker compose up -d --build"
ssh_q "cd ~/$REMOTE_DIR && docker compose ps"
echo "==> 完成：${SERVICE_URL%/}/"
