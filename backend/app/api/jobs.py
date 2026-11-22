"""任務 API：上傳、列表、進度（SSE）、取消、修改排隊中任務的設定、重跑、刪除、音檔、逐字稿編輯與匯出。"""
from __future__ import annotations

import asyncio
import json
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, AsyncIterator, BinaryIO, Literal

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import dictionary_store, export, i18n, storage, transcript as tr, voiceprint, youtube as yt
from ..config import settings
from ..i18n import ApiError, resolve_lang
from ..jobs import estimate, queue

router = APIRouter(prefix="/api")

FINAL_STATUSES = ("done", "failed", "canceled")
CHUNK = 1024 * 1024


class UploadTooLarge(Exception):
    pass


def save_upload(src: BinaryIO, dest: Path, limit: int) -> int:
    """把上傳的檔案寫到 dest（先寫 .part 再改名）；超過 limit bytes 丟 UploadTooLarge。回傳大小。"""
    tmp = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with tmp.open("wb") as fh:
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise UploadTooLarge()
                fh.write(chunk)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return size


def _loaded_models(request: Request | None) -> list[str] | None:
    """目前已載入的模型（估算第一個任務要不要載入模型用）；測試用的假後端沒有 ModelManager 時回傳 None。"""
    manager = getattr(request.app.state, "model_manager", None) if request is not None else None
    return manager.loaded() if manager is not None else None


def _public_jobs(jobs: list[dict[str, Any]], request: Request | None = None,
                 active: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """加上排隊順位與預估時間，並把 stage／error／warnings 依請求的語言 render。
    active 沒給時另外查（jobs 只是部分任務時要用完整的佇列計算）。"""
    if active is None:
        active = queue.list_active() if any(j["status"] in queue.ACTIVE_STATUSES for j in jobs) else []
    info = estimate.queue_info(active, loaded=_loaded_models(request)) if active else {}
    lang = resolve_lang(request)
    return [{**j, **info.get(j["id"], estimate.EMPTY_INFO), "caption_lang": _caption_lang(j), **_texts(j, lang)}
            for j in jobs]


def _texts(job: dict[str, Any], lang: str) -> dict[str, Any]:
    """任務裡給使用者看的文字依語言 render。stage_code 是處理步驟的代碼（例如 stage.transcribe），
    前端判斷目前在哪一步用；舊資料（stage 是中文字串）為 None。"""
    stage = job.get("stage")
    return {
        "stage": i18n.localize(stage, lang),
        "stage_code": stage["code"] if i18n.is_message(stage) else None,
        "error": i18n.localize(job.get("error"), lang),
        "warnings": i18n.localize_list(job.get("warnings"), lang),
        "speaker_summary": tr.localize_summary(job.get("speaker_summary"), lang),
    }


def _caption_lang(job: dict[str, Any]) -> str | None:
    """YouTube 來源可以用哪個語言的人工字幕（重新轉錄時「用字幕」選項用）；沒有為 None。"""
    if job.get("source") != "youtube":
        return None
    return yt.choose_caption(list(job.get("yt_manual_captions") or []), job.get("yt_language"))


def _public_job(job: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    return _public_jobs([job], request)[0]


def _all_public_jobs(request: Request | None = None) -> list[dict[str, Any]]:
    jobs = queue.list_all()
    storage.ensure_speaker_summaries(jobs)
    # list_all 已包含全部任務：從裡面挑出佇列（處理中在前，排隊中依領取順序），不用再查一次
    active = sorted((j for j in jobs if j["status"] in queue.ACTIVE_STATUSES),
                    key=lambda j: (j["status"] == "queued", j["position"], j["queued_at"], j["id"]))
    return _public_jobs(jobs, request, active=active)


def _get_job(job_id: str) -> dict[str, Any]:
    job = queue.get(job_id)
    if job is None:
        raise ApiError(404, "job.not_found")
    return job


def _validate_options(model: str, num_speakers: int | None, dictionary_ids: list[int]) -> None:
    opt = settings.model_option(model)
    if opt is None:
        raise ApiError(400, "job.model.unsupported", model=model)
    if not opt.available():
        raise ApiError(400, "job.model.not_installed", label=opt.label_msg())
    if num_speakers is not None and not (1 <= num_speakers <= 20):
        raise ApiError(400, "job.num_speakers.range", min=1, max=20)


def _parse_ids(raw: str) -> list[int]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        if raw.startswith("["):
            return [int(x) for x in json.loads(raw)]
        return [int(x) for x in raw.split(",") if x.strip()]
    except (ValueError, TypeError) as e:
        raise ApiError(400, "job.dictionary_ids.invalid") from e


@router.get("/models")
def list_models(request: Request) -> dict[str, Any]:
    lang = resolve_lang(request)
    return {
        "default": settings.effective_default_model(),
        "max_upload_mb": settings.max_upload_mb,
        "models": [
            {"id": o.id, "label": i18n.localize(o.label_msg(), lang),
             "description": i18n.localize(o.description_msg(), lang), "available": o.available()}
            for o in settings.model_options()
        ],
    }


@router.post("/jobs")
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(None),
    diarize: bool = Form(True),
    num_speakers: int | None = Form(None),
    dictionary_ids: str = Form(""),
    batch_id: str | None = Form(None),
) -> dict[str, Any]:
    model = model or settings.effective_default_model()
    ids = _parse_ids(dictionary_ids)
    batch_id = (batch_id or "").strip() or None
    if batch_id is not None and not queue.BATCH_ID_RE.match(batch_id):
        raise ApiError(400, "job.batch_id.invalid")
    if num_speakers is not None and num_speakers <= 0:
        num_speakers = None
    _validate_options(model, num_speakers, ids)

    filename = Path(file.filename or "audio").name or "audio"
    ext = Path(filename).suffix.lower()[:10]
    job_id = queue.new_id()
    dest_dir = storage.job_dir(job_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"original{ext}"
    limit = settings.max_upload_mb * 1024 * 1024
    try:
        # 複製檔案在執行緒裡做，不阻塞 event loop（大檔案時其他請求與 SSE 照常回應）
        size = await asyncio.to_thread(save_upload, file.file, dest, limit)
    except UploadTooLarge as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ApiError(413, "upload.too_large", limit_mb=settings.max_upload_mb) from e
    except BaseException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    if size == 0:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ApiError(400, "upload.empty")

    # 先確認 ffmpeg 讀得到音軌，壞檔直接擋下來
    from ..pipeline import audio as audio_mod

    try:
        info = await asyncio.to_thread(audio_mod.probe, dest)
        if info.audio_streams == 0:
            raise audio_mod.AudioError("audio.no_stream")
    except audio_mod.AudioError as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ApiError(400, e.msg) from e

    job = queue.create(
        job_id=job_id, filename=filename, model=model, diarize=diarize, num_speakers=num_speakers,
        dictionary_ids=ids, audio_ext=ext, size_bytes=size, batch_id=batch_id,
    )
    if info.duration:
        queue.update(job_id, duration=info.duration)
        job["duration"] = info.duration
    return _public_job(job, request)


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict[str, Any]]:
    return _all_public_jobs(request)


def _signature(job: dict[str, Any]) -> tuple:
    """SSE 去重用；job 是 _public_job 的輸出（stage、error 已經 render 成字串）。"""
    return (job["status"], job["stage"], job["progress"], job["has_transcript"], job["has_audio"],
            job.get("error"), job.get("queue_position"), job.get("queue_rank"), job.get("cancel_requested"),
            job.get("eta_start_seconds"), job.get("eta_finish_seconds"), job.get("estimate_seconds"),
            job.get("duration"), job.get("filename"), job.get("speaker_summary"),
            # 排隊中修改設定（PATCH /options）也要推送；預估時間沒變的欄位（例如字典）同樣要讓前端更新
            job.get("model"), job.get("diarize"), job.get("num_speakers"), job.get("dictionary_ids"),
            job.get("use_captions"))


async def _sse(gen: AsyncIterator[tuple[str, Any]]) -> AsyncIterator[str]:
    async for event, data in gen:
        if event == "ping":
            yield ": ping\n\n"
        else:
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_response(gen: AsyncIterator[tuple[str, Any]]) -> StreamingResponse:
    return StreamingResponse(
        _sse(gen),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/events")
async def all_events(request: Request) -> StreamingResponse:
    """所有任務的進度（首頁用）：第一個事件是完整清單（full: true），之後只送有變動的任務。"""

    async def gen() -> AsyncIterator[tuple[str, Any]]:
        sigs: dict[str, tuple] = {}
        idle = 0
        first = True
        while not await request.is_disconnected():
            jobs = await asyncio.to_thread(_all_public_jobs, request)
            changed = []
            ids = set()
            for j in jobs:
                ids.add(j["id"])
                sig = _signature(j)
                if sigs.get(j["id"]) != sig:
                    sigs[j["id"]] = sig
                    changed.append(j)
            removed = [k for k in sigs if k not in ids]
            for k in removed:
                del sigs[k]
            if first:
                # 連線（含斷線重連）後的第一個事件是完整清單：前端整份替換，斷線期間被刪掉的任務才不會留著
                first = False
                idle = 0
                yield "jobs", {"changed": changed, "removed": [], "full": True}
            elif changed or removed:
                idle = 0
                yield "jobs", {"changed": changed, "removed": removed}
            else:
                idle += 1
                if idle % 15 == 0:
                    yield "ping", None
            await asyncio.sleep(1.0)

    return _sse_response(gen())


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    return _public_job(_get_job(job_id), request)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """單一任務進度（SSE）：狀態有變就送 `job` 事件，結束後關閉串流。"""
    _get_job(job_id)

    async def gen() -> AsyncIterator[tuple[str, Any]]:
        last = None
        idle = 0
        while not await request.is_disconnected():
            job = await asyncio.to_thread(queue.get, job_id)
            if job is None:
                yield "deleted", {"id": job_id}
                return
            job = await asyncio.to_thread(_public_job, job, request)
            sig = _signature(job)
            if sig != last:
                last = sig
                idle = 0
                yield "job", job
                if job["status"] in FINAL_STATUSES:
                    return
            else:
                idle += 1
                if idle % 15 == 0:
                    yield "ping", None
            await asyncio.sleep(0.7)

    return _sse_response(gen())


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    _get_job(job_id)
    queue.request_cancel(job_id)
    return _public_job(_get_job(job_id), request)


class MoveBody(BaseModel):
    # 三選一：to（front＝移到最前、back＝移到最後、up／down＝上移／下移一位）、before、after（另一個排隊中任務的 id）
    to: Literal["front", "back", "up", "down"] | None = None
    before: str | None = None
    after: str | None = None


@router.post("/jobs/{job_id}/move")
def move_job(job_id: str, body: MoveBody, request: Request) -> dict[str, Any]:
    """調整排隊順序。只有排隊中的任務能移動；回傳調整後的佇列（處理中＋排隊中）。"""
    if sum(x is not None for x in (body.to, body.before, body.after)) != 1:
        raise ApiError(400, "job.move.target_required")
    try:
        order = queue.move(job_id, to=body.to, before=body.before, after=body.after)
    except KeyError as e:
        raise ApiError(404, "job.not_found") from e
    except queue.MoveError as e:
        raise ApiError(409, e.msg) from e
    active = queue.list_active()
    return {"order": order, "jobs": _public_jobs(active, request, active=active)}


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, request: Request) -> dict[str, Any]:
    """整批取消：這批裡排隊中的直接取消，處理中的在下一個檢查點停止；已結束的不動。"""
    result = queue.cancel_batch(batch_id)
    if result is None:
        raise ApiError(404, "batch.not_found")
    return {**result, "jobs": _public_jobs(queue.list_batch(batch_id), request)}


class OptionsBody(BaseModel):
    """修改排隊中任務的設定：欄位都可省略，省略（或 null）＝不改。
    num_speakers 例外：省略＝不改，null 或 0＝自動判斷人數。
    use_captions 只對 YouTube 來源有意義，其他來源傳了也忽略（同重新轉錄）。"""
    model: str | None = None
    diarize: bool | None = None
    num_speakers: int | None = None
    dictionary_ids: list[int] | None = None
    use_captions: bool | None = None


@router.patch("/jobs/{job_id}/options")
def update_job_options(job_id: str, body: OptionsBody, request: Request) -> dict[str, Any]:
    """修改排隊中、還沒開始處理的任務設定（模型、講者辨識、人數、字典、YouTube 是否用字幕）。

    只改這一筆（同批其他任務不變），不影響排隊順序。驗證規則同上傳／重新轉錄；
    寫入時在交易裡確認仍在排隊中，worker 已經領走的話回 409、資料不變。
    """
    job = _get_job(job_id)
    if job["status"] != "queued":
        raise ApiError(409, "job.options.already_started" if job["status"] == "running"
                       else "job.options.already_finished")
    is_youtube = job.get("source") == "youtube"
    fields: dict[str, Any] = {}
    if body.model is not None:
        fields["model"] = body.model
    if body.diarize is not None:
        fields["diarize"] = body.diarize
    if "num_speakers" in body.model_fields_set:
        fields["num_speakers"] = body.num_speakers if body.num_speakers and body.num_speakers > 0 else None
    # 字典：這次「新加入」的 id 不存在才擋（使用者剛選的字典被刪了，要重新選）；
    # 任務原本就存著、後來被刪除的字典直接剔除，不然只改模型也存不了（load_merged 本來就會略過）
    stored_ids = job["dictionary_ids"] or []
    if body.dictionary_ids is not None:
        exists = {i: dictionary_store.get(i) is not None for i in body.dictionary_ids}
        if any(not ok and i not in stored_ids for i, ok in exists.items()):
            raise ApiError(400, "job.options.dictionary_missing")
        fields["dictionary_ids"] = [i for i in body.dictionary_ids if exists[i]]
    else:
        kept = [i for i in stored_ids if dictionary_store.get(i) is not None]
        if kept != stored_ids:
            fields["dictionary_ids"] = kept
    caption_lang = _caption_lang(job)
    if is_youtube and body.use_captions is not None:
        if body.use_captions and caption_lang is None:
            raise ApiError(400, "job.captions.unavailable")
        fields["use_captions"] = body.use_captions

    # 用修改後的完整設定驗證（沒改的欄位沿用原本的值）
    model = fields.get("model", job["model"])
    num = fields.get("num_speakers", job["num_speakers"])
    caption_route = is_youtube and fields.get("use_captions", job["use_captions"]) and caption_lang is not None
    if caption_route and settings.model_option(model) is not None:
        # 走字幕路線不需要 Whisper：模型還沒安裝也可以（同建立 YouTube 任務）
        if num is not None and not (1 <= num <= 20):
            raise ApiError(400, "job.num_speakers.range", min=1, max=20)
    else:
        _validate_options(model, num, fields.get("dictionary_ids", job["dictionary_ids"]))

    try:
        updated = queue.update_queued_options(job_id, **fields)
    except KeyError as e:
        raise ApiError(404, "job.not_found") from e
    except queue.NotQueuedError as e:
        raise ApiError(409, e.msg) from e
    if updated is None:  # 剛改完就被刪除
        raise ApiError(404, "job.not_found")
    return _public_job(updated, request)


class RerunBody(BaseModel):
    model: str | None = None
    diarize: bool = True
    num_speakers: int | None = None
    dictionary_ids: list[int] = Field(default_factory=list)
    # YouTube 來源：True＝有人工字幕就用字幕、False＝用模型轉錄；None＝沿用原本的設定
    use_captions: bool | None = None


@router.post("/jobs/{job_id}/rerun")
def rerun_job(job_id: str, body: RerunBody, request: Request) -> dict[str, Any]:
    job = _get_job(job_id)
    if job["status"] in queue.ACTIVE_STATUSES:
        raise ApiError(409, "job.rerun.active")
    is_youtube = job.get("source") == "youtube"
    # YouTube 來源還沒下載成功（例如上次下載失敗）時，重新轉錄會重新下載
    if not job["has_audio"] or (not is_youtube and not storage.original_path(job).exists()):
        raise ApiError(409, "job.rerun.audio_deleted")
    model = body.model or job["model"]
    num = body.num_speakers if body.num_speakers and body.num_speakers > 0 else None
    _validate_options(model, num, body.dictionary_ids)
    use_captions = body.use_captions if is_youtube else None
    if use_captions and yt.choose_caption(job.get("yt_manual_captions") or [], job.get("yt_language")) is None:
        raise ApiError(400, "job.captions.unavailable")
    if not queue.requeue(job_id, model=model, diarize=body.diarize, num_speakers=num,
                         dictionary_ids=body.dictionary_ids, use_captions=use_captions):
        # 檢查之後狀態被別人改掉了（例如另一個分頁剛按了重新轉錄、或音檔剛被刪掉）
        latest = _get_job(job_id)
        if latest["status"] in queue.ACTIVE_STATUSES:
            raise ApiError(409, "job.rerun.active")
        raise ApiError(409, "job.rerun.audio_deleted")
    return _public_job(_get_job(job_id), request)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request,
               mode: str = Query(..., pattern="^(transcript|audio|all)$")) -> dict[str, Any]:
    try:
        job = storage.delete_job(job_id, mode)
    except KeyError as e:
        raise ApiError(404, "job.not_found") from e
    except storage.JobBusy as e:
        raise ApiError(409, e.msg) from e
    return {"deleted": mode, "job": _public_job(job, request) if job else None}


@router.get("/jobs/{job_id}/audio")
def get_audio(job_id: str) -> FileResponse:
    job = _get_job(job_id)
    path = storage.playback_path(job_id)
    if not job["has_audio"] or not path.exists():
        raise ApiError(404, "job.audio.unavailable")
    return FileResponse(path, media_type="audio/mp4", headers={"Cache-Control": "no-cache"})


# ---- 逐字稿 ----


def _load_transcript(job_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    _get_job(job_id)
    data = storage.load(job_id)
    if data is None:
        raise ApiError(404, "transcript.not_found")
    return data


def _strip(t: dict[str, Any]) -> dict[str, Any]:
    t = dict(t)
    t.pop("speaker_embeddings", None)
    return t


def _transcript_payload(original: dict[str, Any], edited: dict[str, Any] | None,
                        request: Request | None = None) -> dict[str, Any]:
    """逐字稿回應：warnings、講者辨識失敗原因、文字來源說明依請求的語言 render（內容本身不翻譯）。"""
    lang = resolve_lang(request)
    current = edited if edited is not None else tr.make_edited(original)
    with_emb = tr.voiceprint_labels(original, current)
    return {
        "original": tr.localize_texts(_strip(original), lang),
        "current": tr.localize_texts(_strip(current), lang),
        "edited": edited is not None,
        "voiceprint_speakers": with_emb,
    }


@router.get("/jobs/{job_id}/transcript")
def get_transcript(job_id: str, request: Request) -> dict[str, Any]:
    original, edited = _load_transcript(job_id)
    return _transcript_payload(original, edited, request)


def _edit_or_404(job_id: str, fn) -> tuple[dict[str, Any], dict[str, Any], Any]:
    _get_job(job_id)
    try:
        out = storage.edit(job_id, fn)
    except storage.JobBusy as e:
        # 排隊或重新轉錄中：完成時會用新結果取代逐字稿，現在的編輯會被清掉
        raise ApiError(409, e.msg) from e
    if out is None:
        raise ApiError(404, "transcript.not_found")
    return out


@router.post("/jobs/{job_id}/transcript/edit")
def edit_transcript(job_id: str, op: dict[str, Any], request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    def apply(original: dict[str, Any], current: dict[str, Any], conn: Any) -> None:
        try:
            tr.apply_edit(current, op, reserved=tr.reserved_labels(original))
        except tr.EditError as e:
            raise ApiError(400, e.msg) from e
        except (KeyError, ValueError, TypeError) as e:  # 缺欄位、型別不對
            raise ApiError(400, "transcript.edit.invalid") from e

    original, current, _ = _edit_or_404(job_id, apply)
    return _transcript_payload(original, current, request)


@router.post("/jobs/{job_id}/transcript/reset")
def reset_transcript(job_id: str, request: Request) -> dict[str, Any]:
    original, _ = _load_transcript(job_id)
    try:
        storage.save_edited(job_id, None)
    except storage.JobBusy as e:
        raise ApiError(409, e.msg) from e
    return _transcript_payload(original, None, request)


@router.get("/jobs/{job_id}/export/{fmt}")
def export_transcript(job_id: str, fmt: str, request: Request, timestamps: bool = True) -> Response:
    """匯出。?lang=（或 Accept-Language）影響沒取名講者的名稱（講者 N／Speaker N）與固定字樣。"""
    if fmt not in export.FORMATS:
        raise ApiError(400, "export.format.unsupported")
    job = _get_job(job_id)
    original, edited = _load_transcript(job_id)
    current = edited if edited is not None else tr.make_edited(original)
    if job.get("source") == "youtube":
        # 影片標題可能有「.」「/」，不能用 Path().stem
        stem = yt.safe_filename(job.get("yt_title") or job["filename"])
    else:
        stem = Path(job["filename"]).stem or "transcript"
    body = export.render(current, fmt, title=stem, timestamps=timestamps, lang=resolve_lang(request))
    media, ext = export.FORMATS[fmt]
    name = f"{stem}.{ext}"
    disp = f"attachment; filename=\"transcript.{ext}\"; filename*=UTF-8''{urllib.parse.quote(name)}"
    return Response(content=body.encode("utf-8"), media_type=media, headers={"Content-Disposition": disp})


class SaveVoiceprintBody(BaseModel):
    name: str
    # 有值時更新這筆聲紋（講者已比對到聲紋庫的人），不新增重複的一筆
    voiceprint_id: int | None = None


@router.post("/jobs/{job_id}/speakers/{label}/voiceprint")
def save_speaker_voiceprint(job_id: str, label: str, body: SaveVoiceprintBody, request: Request) -> dict[str, Any]:
    """把結果頁的某位講者存進聲紋庫（或更新既有的聲紋），同時把這份逐字稿裡的講者改成這個名字。

    只接受原始結果裡有 embedding、而且在目前版本仍是同一個人的講者代號（見 transcript.voiceprint_labels）。
    """
    name = body.name.strip()
    if not name:
        raise ApiError(400, "voiceprint.name_required")

    def apply(original: dict[str, Any], current: dict[str, Any], conn: Any) -> dict[str, Any]:
        emb = (original.get("speaker_embeddings") or {}).get(label)
        if not emb:
            raise ApiError(400, "voiceprint.speaker.no_embedding")
        if label not in tr.voiceprint_labels(original, current):
            raise ApiError(400, "voiceprint.speaker.not_original")
        model = original.get("embedding_model") or settings.embedding_model
        source = f"job:{job_id}"
        vp = None
        if body.voiceprint_id is not None:
            vp = voiceprint.update_embedding(body.voiceprint_id, name, emb, model, source, None, conn=conn)
        if vp is None:
            # 沒指定，或要更新的那筆已經被刪掉了：新增一筆
            vp = voiceprint.create(name, emb, model, source=source, duration=None, conn=conn)
        tr.rename_speaker(current, label, name)
        current["speakers"][label]["voiceprint_id"] = vp["id"]
        return vp

    original, current, vp = _edit_or_404(job_id, apply)
    payload = _transcript_payload(original, current, request)
    payload["voiceprint"] = {k: v for k, v in vp.items() if k != "embedding"}
    payload["voiceprint_updated"] = body.voiceprint_id is not None and vp["id"] == body.voiceprint_id
    return payload
