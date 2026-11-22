# syntax=docker/dockerfile:1.7
# voice-to-text：前端（Vite 建置）＋後端（FastAPI＋faster-whisper＋WhisperX＋pyannote）單一容器

# ---- 前端建置 ----
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,id=v2t-npm,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Python 推論環境（最花時間的一層，程式碼變動不會重建）----
FROM python:3.12-slim-bookworm AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements-gpu.txt backend/constraints.txt ./
# torch 2.8 + CUDA 12.8（whisperx 3.8 需要 torch~=2.8）；constraints 防止後續套件把 torch 換掉
RUN --mount=type=cache,id=v2t-pip,target=/root/.cache/pip \
    pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 \
      --index-url https://download.pytorch.org/whl/cu128 \
 && pip install -c constraints.txt -r requirements-gpu.txt -r requirements.txt
# WhisperX 對齊時用 nltk 切句，先下載 punkt_tab（放在 nltk 預設搜尋路徑）
RUN python -m nltk.downloader -d /usr/local/share/nltk_data punkt_tab
# CTranslate2（faster-whisper）要從 pip 安裝的 nvidia 套件找 cuBLAS／cuDNN
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib

# ---- 最終映像 ----
FROM base AS app
# 測試工具（在容器內跑 pytest 用，體積很小）
RUN --mount=type=cache,id=v2t-pip,target=/root/.cache/pip pip install "pytest>=8" "httpx>=0.27"
# YouTube 下載：yt-dlp＋deno（JS challenge）＋curl-cffi，版本與原因見 backend/requirements-youtube.txt
COPY backend/requirements-youtube.txt ./
RUN --mount=type=cache,id=v2t-pip,target=/root/.cache/pip \
    pip install -c constraints.txt -r requirements-youtube.txt \
 && yt-dlp --version && deno --version
COPY backend/app /app/app
COPY backend/tests /app/tests
COPY --from=frontend /fe/dist /app/static
# 與 benchmark 共用 models volume 的配置：HF 快取在 /models/hf、Breeze CTranslate2 在 /models/ct2
ENV DATA_DIR=/data MODELS_DIR=/models HF_HOME=/models/hf TORCH_HOME=/models/torch \
    XDG_CACHE_HOME=/models/cache MPLCONFIGDIR=/tmp/matplotlib \
    STATIC_DIR=/app/static PYANNOTE_METRICS_ENABLED=false TMPDIR=/data/tmp \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
CMD ["sh", "-c", "mkdir -p /data/tmp && exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --timeout-keep-alive 30"]
