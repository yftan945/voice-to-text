"""YouTube 影片資訊的記憶體快取（API 與 worker 共用，同一個行程）。

- 存完整的 yt-dlp 資訊（已拿掉用不到的大欄位）、摘要與取得時間：
  - API 顯示影片資訊用 30 分鐘內的摘要（SUMMARY_SECONDS）；
  - worker 下載時，資訊在 2 小時內（REUSE_SECONDS）就直接沿用，不重新解析（播放清單的影片在
    前端逐支查過一次，worker 不用再查第二次）。YouTube 的格式網址大約 6 小時後過期，2 小時內很安全；
    萬一還是失效，worker 會重新解析一次再下載（見 pipeline/youtube_source.py）。
- 查詢失敗（私人影片、被限流等）也快取 ERROR_SECONDS，避免前端重試時一直打 YouTube。
- 最多 MAX_ENTRIES 筆，超過時丟掉最舊的。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .youtube import YouTubeError

SUMMARY_SECONDS = 30 * 60
REUSE_SECONDS = 2 * 60 * 60
ERROR_SECONDS = 5 * 60
MAX_ENTRIES = 300

# 下載用不到、但占掉大部分大小的欄位（自動字幕的各種翻譯網址、縮圖清單、熱度圖）
_DROP_KEYS = ("automatic_captions", "thumbnails", "heatmap")


@dataclass
class Entry:
    fetched_at: float
    info: dict[str, Any]
    summary: dict[str, Any]


_entries: dict[str, Entry] = {}
_errors: dict[str, tuple[float, YouTubeError]] = {}
_lock = threading.Lock()


def slim(info: dict[str, Any]) -> dict[str, Any]:
    """拿掉下載用不到的大欄位；格式清單裡的分鏡圖（storyboard）也拿掉。"""
    out = {k: v for k, v in info.items() if k not in _DROP_KEYS}
    formats = out.get("formats")
    if isinstance(formats, list):
        out["formats"] = [f for f in formats
                          if not (isinstance(f, dict) and (f.get("format_note") == "storyboard" or f.get("ext") == "mhtml"))]
    return out


def _prune(now: float) -> None:
    for k in [k for k, e in _entries.items() if now - e.fetched_at >= REUSE_SECONDS]:
        del _entries[k]
    for k in [k for k, (ts, _) in _errors.items() if now - ts >= ERROR_SECONDS]:
        del _errors[k]
    if len(_entries) > MAX_ENTRIES:
        for k, _ in sorted(_entries.items(), key=lambda kv: kv[1].fetched_at)[:len(_entries) - MAX_ENTRIES]:
            del _entries[k]


def put(video_id: str, info: dict[str, Any], summary: dict[str, Any], *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        _errors.pop(video_id, None)
        _entries[video_id] = Entry(fetched_at=now, info=slim(info), summary=summary)
        _prune(now)


def put_error(video_id: str, err: YouTubeError, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        _errors[video_id] = (now, err)
        _prune(now)


def summary(video_id: str, max_age: float = SUMMARY_SECONDS) -> dict[str, Any] | None:
    with _lock:
        e = _entries.get(video_id)
        if e is not None and time.time() - e.fetched_at < max_age:
            return e.summary
    return None


def info(video_id: str, max_age: float = REUSE_SECONDS) -> tuple[dict[str, Any], float] | None:
    """（完整資訊, 取得時間）；沒有或太舊回傳 None。"""
    with _lock:
        e = _entries.get(video_id)
        if e is not None and time.time() - e.fetched_at < max_age:
            return e.info, e.fetched_at
    return None


def error(video_id: str) -> YouTubeError | None:
    with _lock:
        hit = _errors.get(video_id)
        if hit is not None and time.time() - hit[0] < ERROR_SECONDS:
            return hit[1]
    return None


def drop(video_id: str) -> None:
    with _lock:
        _entries.pop(video_id, None)
        _errors.pop(video_id, None)


def clear() -> None:
    with _lock:
        _entries.clear()
        _errors.clear()
