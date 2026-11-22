"""YouTube 任務的前置步驟（worker 在推論前呼叫）：取得影片資訊 → 下載人工字幕 → 下載音訊。

- 音訊已經下載過（重新轉錄）且需要的字幕也在時，完全不連 YouTube；
- 影片資訊在快取裡而且夠新（2 小時內，見 app/youtube_cache.py）時直接沿用，不重新解析；
  沿用的資訊下載失敗（格式網址過期等）時重新解析一次再試；
- 字幕與音訊分成兩次 yt-dlp：字幕下載失敗（例如 429）只記警告、改用模型轉錄，不會讓任務失敗；
  只缺字幕（重新轉錄）時字幕失敗也一樣改用模型轉錄；
- 沒有人工字幕時改用模型轉錄，並在警告裡說明。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import storage, youtube_cache
from ..jobs import queue
from ..youtube import YouTubeError, check_playable, choose_caption, language_name, video_summary
from . import ytdlp

log = logging.getLogger(__name__)

DownloadReporter = Callable[[str, float], None]  # (顯示文字, 這一步的進度 0~1)


@dataclass
class Prepared:
    audio_path: Path
    caption_path: Path | None
    caption_lang: str | None
    # 這次取得資訊＋下載音訊花的秒數（估算下載速率用；沒有下載音訊時為 None）
    download_seconds: float | None = None
    # 這次連 YouTube 花的全部秒數（取得資訊、字幕、音訊）；worker 從處理秒數扣掉，字幕路線的速率才不會被高估
    network_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def existing_audio(job: dict[str, Any]) -> Path | None:
    if job.get("audio_ext"):
        p = storage.original_path(job)
        if p.is_file() and p.stat().st_size > 0:
            return p
    # 上次下載完但還沒寫進資料庫（例如容器剛好重啟）也算
    return ytdlp.find_audio(storage.job_dir(job["id"]))


def wanted_caption(job: dict[str, Any]) -> str | None:
    """這個任務要用哪個語言的人工字幕；不用字幕或沒有符合的人工字幕回傳 None。"""
    if not job.get("use_captions"):
        return None
    return choose_caption(list(job.get("yt_manual_captions") or []), job.get("yt_language"))


def needs_download(job: dict[str, Any]) -> bool:
    if job.get("source") != "youtube":
        return False
    if existing_audio(job) is None:
        return True
    lang = wanted_caption(job)
    return lang is not None and ytdlp.find_captions(storage.job_dir(job["id"]), lang) is None


# 沿用快取的資訊下載失敗時，這些錯誤可能只是資訊過期（格式網址失效），重新解析一次再試
_STALE_INFO_CODES = frozenset({"expired", "forbidden", "no_format", "unknown"})

_REASON_LABELS = {
    "rate_limited": "HTTP 429，請求次數過多", "timeout": "逾時", "network": "連線失敗",
    "bot_check": "YouTube 要求驗證", "forbidden": "HTTP 403", "expired": "下載網址失效",
}


def failure_reason(e: YouTubeError) -> str:
    """警告裡的簡短原因，例如「HTTP 429」。"""
    m = re.search(r"HTTP Error (\d{3})", e.detail or "")
    if m and e.code != "rate_limited":
        return f"HTTP {m.group(1)}"
    return _REASON_LABELS.get(e.code) or e.detail or e.code


def _monotonic(report: DownloadReporter) -> DownloadReporter:
    """進度只往前走（字幕與音訊分開下載，各自的進度從 0 開始）。"""
    best = [0.0]

    def wrapped(label: str, p: float) -> None:
        best[0] = max(best[0], p)
        report(label, best[0])

    return wrapped


class _Info:
    """這次下載用的影片資訊；沿用快取的資訊下載失敗時重新解析一次。"""

    def __init__(self, video_id: str, check_cancel: Callable[[], None]) -> None:
        self.video_id = video_id
        self.check_cancel = check_cancel
        hit = youtube_cache.info(video_id)
        self.cached = hit is not None
        self.data: dict[str, Any] = hit[0] if hit is not None else self._fetch()

    def _fetch(self) -> dict[str, Any]:
        info = ytdlp.fetch_video_info(self.video_id, check_cancel=self.check_cancel,
                                      sleep_requests=ytdlp.SLEEP_REQUESTS)
        youtube_cache.put(self.video_id, info, video_summary(info))
        return info

    def download(self, dest: Path, **kw: Any) -> None:
        try:
            ytdlp.download(self.data, dest, **kw)
        except YouTubeError as e:
            if not self.cached or e.code not in _STALE_INFO_CODES:
                raise
            log.warning("沿用快取資訊下載 %s 失敗（%s），重新解析後再試：%s", self.video_id, e.code, e.detail)
            self.cached = False
            youtube_cache.drop(self.video_id)
            self.data = self._fetch()
            check_playable(video_summary(self.data))
            ytdlp.download(self.data, dest, **kw)


def prepare(job: dict[str, Any], report: DownloadReporter, check_cancel: Callable[[], None],
            max_bytes: int) -> Prepared:
    """確保音訊（與要用的字幕）在任務資料夾裡；失敗丟 YouTubeError，取消丟 Canceled。

    字幕下載失敗不算失敗：記在 warnings，caption_lang 為 None（改用模型轉錄）。
    """
    job_id = job["id"]
    jd = storage.job_dir(job_id)
    jd.mkdir(parents=True, exist_ok=True)
    audio = existing_audio(job)
    lang = wanted_caption(job)
    cap = ytdlp.find_captions(jd, lang) if lang else None
    if audio is not None and (lang is None or cap is not None):
        return Prepared(audio_path=audio, caption_path=cap, caption_lang=lang if cap else None)

    report = _monotonic(report)
    t_start = time.monotonic()
    report("取得影片資訊中", 0.0)
    info = _Info(job["video_id"], check_cancel)
    info_seconds = time.monotonic() - t_start
    summary = video_summary(info.data)
    check_playable(summary)
    fields: dict[str, Any] = {
        "filename": summary["title"], "yt_title": summary["title"], "yt_channel": summary["channel"],
        "yt_language": summary["language"], "yt_manual_captions": summary["manual_captions"],
        "source_url": summary["url"],
    }
    if summary["duration"]:
        fields["duration"] = summary["duration"]
    queue.update(job_id, **fields)
    job = {**job, **fields}
    check_cancel()

    # 用最新的字幕清單重新選語言（建立任務後上傳者可能新增或刪掉字幕）
    lang = wanted_caption(job)
    cap = ytdlp.find_captions(jd, lang) if lang else None
    need_audio = audio is None
    need_caps = lang is not None and cap is None
    warnings: list[str] = []

    # 1. 字幕（先下載，失敗只記警告；要下載音訊時字幕只占進度條前面一小段）
    if need_caps:
        assert lang is not None
        lo, hi = (0.02, 0.05) if need_audio else (0.05, 1.0)
        report("下載字幕中", lo)
        try:
            info.download(jd, audio=False, caption_lang=lang,
                          on_progress=lambda p: report("下載字幕中", lo + (hi - lo) * p),
                          check_cancel=check_cancel, max_bytes=max_bytes, duration=summary["duration"])
            cap = ytdlp.find_captions(jd, lang)
            if cap is None:
                warnings.append(f"無法下載 YouTube 人工字幕（{language_name(lang)}），已改用模型轉錄。")
        except YouTubeError as e:
            log.warning("任務 %s 字幕下載失敗（%s）：%s", job_id, e.code, e.detail)
            cap = None
            warnings.append(f"YouTube 人工字幕（{language_name(lang)}）下載失敗（{failure_reason(e)}），"
                            "已改用模型轉錄。")
        if cap is None:
            lang = None

    # 2. 音訊
    download_seconds: float | None = None
    if need_audio:
        report("下載音訊中", 0.05)
        t_audio = time.monotonic()
        info.download(jd, audio=True, caption_lang=None,
                      on_progress=lambda p: report("下載音訊中", 0.05 + 0.95 * p),
                      check_cancel=check_cancel, max_bytes=max_bytes, duration=summary["duration"])
        # 下載速率用「取得資訊＋下載音訊」的時間（不含字幕），與 estimate 的固定開銷定義一致
        download_seconds = info_seconds + (time.monotonic() - t_audio)
        audio = ytdlp.find_audio(jd)
        assert audio is not None  # download() 找不到音訊會丟 YouTubeError
        queue.update(job_id, audio_ext=audio.suffix.lower()[:10], size_bytes=audio.stat().st_size,
                     download_pending=False)
    assert audio is not None
    return Prepared(audio_path=audio, caption_path=cap, caption_lang=lang if cap else None,
                    download_seconds=download_seconds, network_seconds=time.monotonic() - t_start,
                    warnings=warnings)
