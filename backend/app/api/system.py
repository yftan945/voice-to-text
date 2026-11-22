"""系統狀態與健康檢查。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import settings

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    """健康檢查：worker 執行緒應該在跑卻已經停掉時回 503（容器 healthcheck 會標成 unhealthy）。"""
    worker = getattr(request.app.state, "worker", None)
    if worker is None or not worker.started:
        state = "not_started"
    elif worker.is_alive():
        state = "alive"
    else:
        return JSONResponse({"status": "error", "worker": "dead"}, status_code=503)
    return JSONResponse({"status": "ok", "worker": state})


@router.get("/api/status")
def status(request: Request) -> dict[str, Any]:
    worker = getattr(request.app.state, "worker", None)
    manager = getattr(request.app.state, "model_manager", None)
    return {
        "current_job": worker.current_job if worker else None,
        "loaded_models": manager.loaded() if manager else [],
        "idle_unload_seconds": settings.idle_unload_seconds,
        "hf_token_configured": bool(settings.hf_token),
        "diarization_model": settings.diarization_model,
        "embedding_model": settings.embedding_model,
        "voiceprint_threshold": settings.voiceprint_threshold,
        "default_model": settings.effective_default_model(),
        "ytdlp_version": _ytdlp_version(),
    }


def _ytdlp_version() -> str | None:
    from ..pipeline import ytdlp

    return ytdlp.version()
