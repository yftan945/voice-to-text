"""聲紋庫 API：上傳 10–30 秒樣本登記、改名、刪除。"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import voiceprint
from ..config import settings
from ..pipeline import audio as audio_mod
from ..pipeline.errors import GPU_BUSY_MESSAGE, is_oom
from .jobs import UploadTooLarge, save_upload

router = APIRouter(prefix="/api/voiceprints")

# 與聲紋庫頁面的說明一致：10–30 秒單人語音，超過 30 秒只取前 30 秒
MIN_SECONDS = 10.0
MAX_SECONDS = 30.0
MAX_UPLOAD = 100 * 1024 * 1024


def _public(vp: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in vp.items() if k != "embedding"}
    out["compatible"] = vp["embedding_model"] == settings.embedding_model
    return out


class RenameBody(BaseModel):
    name: str


@router.get("")
def list_voiceprints() -> list[dict[str, Any]]:
    return [_public(v) for v in voiceprint.list_all()]


@router.post("")
async def enroll(request: Request, name: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise HTTPException(400, "請輸入名稱")
    work = settings.work_dir / f"vp-{uuid.uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        src = work / ("sample" + Path(file.filename or "").suffix.lower()[:10])
        try:
            size = await asyncio.to_thread(save_upload, file.file, src, MAX_UPLOAD)
        except UploadTooLarge as e:
            raise HTTPException(413, f"樣本檔案太大（上限 {MAX_UPLOAD // 1024 // 1024} MB）") from e
        if size == 0:
            raise HTTPException(400, "檔案是空的")
        wav = work / "sample.wav"
        try:
            await asyncio.to_thread(audio_mod.convert_to_wav, src, wav)
        except audio_mod.AudioError as e:
            raise HTTPException(400, str(e)) from e
        audio = await asyncio.to_thread(audio_mod.load_wav, wav)
        seconds = len(audio) / audio_mod.SAMPLE_RATE
        if seconds < MIN_SECONDS:
            lo, hi = int(MIN_SECONDS), int(MAX_SECONDS)
            raise HTTPException(
                400,
                f"樣本太短（{seconds:.1f} 秒），至少要 {lo} 秒。"
                f"請上傳 {lo}–{hi} 秒、只有這個人說話的錄音（超過 {hi} 秒只會取前 {hi} 秒）。",
            )
        audio = audio[: int(MAX_SECONDS * audio_mod.SAMPLE_RATE)]
        backend = request.app.state.backend
        try:
            emb = await asyncio.to_thread(backend.embed, audio)
        except Exception as e:  # noqa: BLE001
            if is_oom(e):
                raise HTTPException(503, GPU_BUSY_MESSAGE) from e
            raise HTTPException(500, f"計算聲紋失敗：{e}") from e
        vp = voiceprint.create(name, emb, backend.embedding_model, source="upload",
                               duration=round(min(seconds, MAX_SECONDS), 2))
        return _public(vp)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@router.patch("/{vp_id}")
def rename(vp_id: int, body: RenameBody) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(400, "請輸入名稱")
    if not voiceprint.rename(vp_id, body.name.strip()):
        raise HTTPException(404, "找不到聲紋")
    return {"id": vp_id, "name": body.name.strip()}


@router.delete("/{vp_id}")
def delete(vp_id: int) -> dict[str, Any]:
    if not voiceprint.delete(vp_id):
        raise HTTPException(404, "找不到聲紋")
    return {"deleted": vp_id}
