"""FastAPI 進入點：API、前端靜態檔、背景 GPU worker。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import dictionaries, jobs, system, voiceprints, youtube
from .config import settings
from .jobs.worker import Worker
from .pipeline.backend import Backend, GpuBackend
from .pipeline.models import ModelManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voice-to-text")


# multipart 表單除了檔案本身還有邊界與其他欄位，Content-Length 預留一點空間
FORM_OVERHEAD = 1024 * 1024


def _upload_limit(method: str, path: str) -> tuple[int, str] | None:
    """各上傳端點的大小上限（bytes）與錯誤訊息；不是上傳端點回傳 None。"""
    if method != "POST":
        return None
    if path == "/api/jobs":
        return settings.max_upload_mb * 1024 * 1024, f"檔案超過 {settings.max_upload_mb} MB"
    if path == "/api/voiceprints":
        from .api.voiceprints import MAX_UPLOAD

        return MAX_UPLOAD, f"樣本檔案太大（上限 {MAX_UPLOAD // 1024 // 1024} MB）"
    return None


class UploadLimitMiddleware:
    """在讀取 request body 之前先看 Content-Length，超過上限直接回 413。

    FastAPI 會在進入端點函式之前就把整個 multipart 表單讀完（寫進暫存檔），端點裡的檢查來不及擋。
    沒有 Content-Length（chunked）的請求仍由端點邊寫邊檢查。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            limit = _upload_limit(scope["method"], scope["path"])
            if limit is not None:
                raw = dict(scope.get("headers") or []).get(b"content-length")
                try:
                    length = int(raw) if raw is not None else None
                except ValueError:
                    length = None
                if length is not None and length > limit[0] + FORM_OVERHEAD:
                    resp = JSONResponse({"detail": limit[1]}, status_code=413, headers={"Connection": "close"})
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)


# 這些回應不壓縮：SSE 要即時送出（壓縮會被緩衝住）、音檔本身已壓縮且要支援 Range、字型檔已壓縮
NO_COMPRESS_SUFFIXES = ("/events", "/audio", ".woff2", ".woff")


def _skip_compress(scope: Any) -> bool:
    path = scope.get("path") or ""
    if path.endswith(NO_COMPRESS_SUFFIXES):
        return True
    # Range 請求（206 部分內容）壓縮後 Content-Range 就對不上了
    return any(k == b"range" for k, _ in scope.get("headers") or [])


class CompressMiddleware:
    """gzip 壓縮 JS、CSS、JSON、匯出檔（瀏覽器有送 Accept-Encoding: gzip 時）；SSE、音檔、Range 請求、字型檔直接放行。"""

    def __init__(self, app: Any, minimum_size: int = 1024) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and not _skip_compress(scope):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def create_app(backend: Backend | None = None, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db()
        manager = None
        be = backend
        if be is None:
            manager = ModelManager(idle_seconds=settings.idle_unload_seconds)
            manager.start()
            be = GpuBackend(manager)
        app.state.backend = be
        app.state.model_manager = manager
        worker = Worker(be)
        app.state.worker = worker
        if start_worker:
            worker.start()
        log.info("voice-to-text 啟動（資料：%s，模型：%s）", settings.data_dir, settings.models_dir)
        try:
            yield
        finally:
            worker.stop()
            if manager is not None:
                manager.stop()

    app = FastAPI(title="voice-to-text", lifespan=lifespan)
    app.add_middleware(UploadLimitMiddleware)
    app.add_middleware(CompressMiddleware)
    app.include_router(system.router)
    app.include_router(jobs.router)
    app.include_router(dictionaries.router)
    app.include_router(voiceprints.router)
    app.include_router(youtube.router)

    static = settings.static_dir
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Any:
        """前端是單頁應用：非 API 路徑一律回 index.html（實體檔案存在就直接回檔案）。"""
        if path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        candidate = (static / path).resolve()
        if path and candidate.is_file() and static.resolve() in candidate.parents:
            return FileResponse(candidate)
        index = static / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, "前端尚未建置")

    return app


app = create_app()
