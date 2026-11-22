#!/usr/bin/env bash
# 從 1Password（op CLI）讀 HF token，經 ssh stdin 寫進 GPU 主機的 .env（chmod 600）。
# token 不會出現在指令列、輸出或 log。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 本機設定（不進 git）：DEPLOY_HOST、DEPLOY_URL、OP_ITEM、OP_VAULT 等，範例見 .deploy.env.example
[ -f "$ROOT/.deploy.env" ] && . "$ROOT/.deploy.env"
HOST="${DEPLOY_HOST:-gpu-host}"
REMOTE_DIR="${DEPLOY_DIR:-docker/voice-to-text}"
ITEM="${OP_ITEM:?請在 .deploy.env 設定 OP_ITEM（1Password 項目名稱）}"
VAULT="${OP_VAULT:?請在 .deploy.env 設定 OP_VAULT（1Password vault）}"

op item get "$ITEM" --vault "$VAULT" --fields credential --reveal \
  | ssh "$HOST" "umask 077; mkdir -p ~/$REMOTE_DIR && cd ~/$REMOTE_DIR && IFS= read -r tok \
      && [ -n \"\$tok\" ] && touch .env && chmod 600 .env \
      && { grep -v '^HF_TOKEN=' .env || true; } > .env.tmp \
      && printf 'HF_TOKEN=%s\n' \"\$tok\" >> .env.tmp && mv .env.tmp .env && chmod 600 .env \
      && echo 'HF_TOKEN 已寫入 .env'" \
  2> >(grep -v -e 'port 8189' -e 'local forwarding' >&2)
