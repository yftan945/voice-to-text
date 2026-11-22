"""YouTube 網址 API：解析網址（含展開播放清單）、查單一影片資訊、建立 YouTube 任務。

影片資訊（標題、長度、人工字幕）要跑一次 yt-dlp（約 3 秒），完整資訊放進記憶體快取（app/youtube_cache.py）：
API 30 分鐘內直接沿用摘要；worker 下載時 2 小時內直接沿用完整資訊，不用再解析一次。查詢失敗也快取 5 分鐘。
同時最多 MAX_CONCURRENT 個 yt-dlp 在跑，避免一次貼很多網址時塞爆。

/youtube/resolve 一次最多 50 行：用專用的執行緒池（MAX_CONCURRENT 條）處理，排隊中的網址不會占用
預設 executor，上傳檔案（asyncio.to_thread）與其他請求不會被卡住。
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import youtube as yt
from .. import youtube_cache
from ..i18n import DEFAULT_LANG, ApiError, localize, msg, resolve_lang
from ..jobs import queue
from ..pipeline import ytdlp
from .jobs import _public_job, _validate_options

router = APIRouter(prefix="/api")

MAX_CONCURRENT = 3
MAX_LINES = 50

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
# /youtube/resolve 專用：一次貼 50 行時最多 MAX_CONCURRENT 條執行緒，其餘在這個池子裡排隊
_resolve_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="yt-resolve")


def video_info(video_id: str) -> dict[str, Any]:
    """影片摘要（有快取）；無法處理的影片丟 YouTubeError（失敗也快取 5 分鐘）。"""
    if not yt.VIDEO_ID_RE.match(video_id):
        raise yt.YouTubeError("invalid", msg("youtube.error.invalid_id"), 400)
    hit = youtube_cache.summary(video_id)
    if hit is not None:
        return hit
    err = youtube_cache.error(video_id)
    if err is not None:
        raise err
    with _slots:
        # 等位子的時候別的請求可能已經查好了
        hit = youtube_cache.summary(video_id)
        if hit is not None:
            return hit
        try:
            info = ytdlp.fetch_video_info(video_id)
        except yt.YouTubeError as e:
            youtube_cache.put_error(video_id, e)
            raise
    summary = yt.video_summary(info)
    if not summary["video_id"]:
        summary["video_id"] = video_id
        summary["url"] = yt.video_url(video_id)
    youtube_cache.put(video_id, info, summary)
    return summary


def _playable_or_error(summary: dict[str, Any], lang: str) -> dict[str, Any]:
    out = dict(summary)
    try:
        yt.check_playable(summary)
        out["error"] = None
    except yt.YouTubeError as e:
        out["error"] = localize(e.msg, lang)
    return out


def playlist_info(playlist_id: str, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    with _slots:
        data = ytdlp.fetch_playlist(playlist_id)
    entries = []
    for e in data.get("entries") or []:
        if not isinstance(e, dict):
            continue
        vid = e.get("id") or ""
        if not yt.VIDEO_ID_RE.match(vid):
            continue
        title = e.get("title") or vid
        duration = e.get("duration")
        # 私人或已刪除的影片在清單裡是「[Private video]」「[Deleted video]」，沒有長度
        unavailable = title in ("[Private video]", "[Deleted video]") or e.get("availability") in (
            "private", "needs_auth", "subscriber_only", "premium_only")
        live = e.get("live_status") in ("is_live", "is_upcoming", "post_live")
        entries.append({
            "video_id": vid, "title": title,
            "duration": float(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
            "channel": e.get("channel") or e.get("uploader") or "",
            "unavailable": unavailable or live,
            "reason": localize(msg("youtube.entry.unavailable"), lang) if unavailable
            else (localize(msg("youtube.entry.live"), lang) if live else None),
        })
    count = data.get("playlist_count")
    return {
        "playlist_id": playlist_id,
        "title": data.get("title") or playlist_id,
        "channel": data.get("channel") or data.get("uploader") or "",
        "count": count if isinstance(count, int) else len(entries),
        "truncated": isinstance(count, int) and count > len(entries),
        "entries": entries,
    }


class ResolveBody(BaseModel):
    # 貼上的文字：一行一個網址（也接受空白或逗號分隔）
    text: str = Field(max_length=200_000)


def _resolve_one(raw: str, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    try:
        parsed = yt.parse_url(raw)
    except yt.InvalidUrl as e:
        return {"input": raw, "kind": "invalid", "error": localize(e.msg, lang)}
    if parsed.kind == "video":
        try:
            return {"input": raw, "kind": "video",
                    "video": _playable_or_error(video_info(parsed.video_id or ""), lang)}
        except yt.YouTubeError as e:
            return {"input": raw, "kind": "video", "video_id": parsed.video_id, "error": localize(e.msg, lang)}
    try:
        return {"input": raw, "kind": "playlist", "playlist": playlist_info(parsed.playlist_id or "", lang),
                "video_id": parsed.video_id}
    except yt.YouTubeError as e:
        message = e.msg if e.code != "removed" else msg("youtube.error.playlist_missing")
        return {"input": raw, "kind": "playlist", "playlist_id": parsed.playlist_id,
                "error": localize(message, lang)}


@router.post("/youtube/resolve")
async def resolve(body: ResolveBody, request: Request) -> dict[str, Any]:
    """解析貼上的網址：單一影片回傳資訊（含有沒有人工字幕）；播放清單只列出影片（不逐支查字幕，
    前端再用 /api/youtube/videos/{id} 逐支補上）；不是 YouTube 網址的回傳錯誤。"""
    lines, truncated = yt.collect_lines(body.text, MAX_LINES)
    if not lines:
        raise ApiError(400, "youtube.resolve.empty")
    # 不用 asyncio.to_thread：50 個等 yt-dlp 的工作會占滿預設 executor，卡住上傳與其他 to_thread 呼叫
    loop = asyncio.get_running_loop()
    lang = resolve_lang(request)
    items = await asyncio.gather(*(loop.run_in_executor(_resolve_pool, _resolve_one, raw, lang) for raw in lines))
    return {"items": items, "truncated": truncated}


@router.get("/youtube/videos/{video_id}")
def get_video(video_id: str, request: Request) -> dict[str, Any]:
    try:
        return _playable_or_error(video_info(video_id), resolve_lang(request))
    except yt.YouTubeError as e:
        raise ApiError(e.status, e.msg) from e


class YoutubeJobBody(BaseModel):
    video_id: str
    model: str | None = None
    diarize: bool = True
    num_speakers: int | None = None
    dictionary_ids: list[int] = Field(default_factory=list)
    batch_id: str | None = None
    # 有人工字幕時直接用字幕（False＝一律用模型轉錄）
    use_captions: bool = True


@router.post("/jobs/youtube")
def create_youtube_job(body: YoutubeJobBody, request: Request) -> dict[str, Any]:
    """建立 YouTube 任務：音訊與字幕由 worker 在處理時下載（下載也是任務的一個步驟，可取消）。"""
    from ..config import settings

    model = body.model or settings.effective_default_model()
    batch_id = (body.batch_id or "").strip() or None
    if batch_id is not None and not queue.BATCH_ID_RE.match(batch_id):
        raise ApiError(400, "job.batch_id.invalid")
    num = body.num_speakers if body.num_speakers and body.num_speakers > 0 else None
    opt = settings.model_option(model)
    if opt is None or opt.available():
        _validate_options(model, num, body.dictionary_ids)
    try:
        summary = video_info(body.video_id)
        yt.check_playable(summary)
    except yt.YouTubeError as e:
        raise ApiError(e.status, e.msg) from e
    if opt is not None and not opt.available():
        # 模型還沒安裝：會走字幕路線（不需要 Whisper）的任務照樣可以建立，其他的照原本規則擋下
        if body.use_captions and summary.get("caption_lang"):
            if num is not None and not (1 <= num <= 20):
                raise ApiError(400, "job.num_speakers.range", min=1, max=20)
        else:
            _validate_options(model, num, body.dictionary_ids)
    job = queue.create(
        filename=summary["title"], model=model, diarize=body.diarize, num_speakers=num,
        dictionary_ids=body.dictionary_ids, audio_ext="", size_bytes=0, batch_id=batch_id,
        duration=summary["duration"],
        youtube={
            "video_id": summary["video_id"], "source_url": summary["url"], "title": summary["title"],
            "channel": summary["channel"], "language": summary["language"],
            "manual_captions": summary["manual_captions"], "use_captions": body.use_captions,
        },
    )
    return _public_job(job, request)
