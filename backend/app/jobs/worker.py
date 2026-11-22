"""單一 GPU worker 執行緒：一次只跑一個任務，其餘在 SQLite 佇列排隊。"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from .. import dictionary_store, storage, voiceprint
from ..config import settings
from ..i18n import LocalizedError, Message, has, is_message, message_of, msg
from ..pipeline import captions as captions_mod
from ..pipeline import runner, youtube_source
from ..pipeline.backend import Backend
from ..pipeline.errors import Canceled, GpuBusy
from ..pipeline.models import free_gpu_memory, load_timer, reset_load_timer
from ..youtube import YouTubeError, language_param
from . import estimate, queue

log = logging.getLogger(__name__)

# 進度寫入 DB 的最小間隔（秒），避免轉錄時太頻繁寫入
PROGRESS_WRITE_INTERVAL = 0.5
# 沒有預期到的錯誤讓任務停在 running 時，標成失敗用的訊息
UNEXPECTED_ERROR = msg("job.error.unexpected")
STAGE_FAILED = msg("stage.failed")
STAGE_CANCELED = msg("stage.canceled")
STAGE_DONE = msg("stage.done")


def _caption_reason(warning: Message | str) -> Message | str:
    """字幕不能用的警告（「…，已改用模型轉錄。」）換成不含「改用模型轉錄」的原因（模型也不能用時組失敗訊息）。"""
    if is_message(warning):
        code = warning["code"]  # type: ignore[index]
        reason_code = f"{code}.reason"
        return msg(reason_code, **(warning.get("params") or {})) if has(reason_code) else warning  # type: ignore[union-attr]
    return re.sub(r"[，,]?(已)?改用模型轉錄。?$", "", str(warning))


class Worker:
    def __init__(self, backend: Backend, poll_interval: float = 5.0) -> None:
        self.backend = backend
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current_job: str | None = None

    def start(self) -> None:
        n = queue.requeue_unfinished()
        if n:
            log.info("重新排隊 %d 個未完成任務", n)
        self._thread = threading.Thread(target=self._loop, name="gpu-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        queue.wakeup.set()

    @property
    def started(self) -> bool:
        return self._thread is not None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            # 整個迴圈包在 try 裡：資料庫暫時鎖住或其他錯誤時不能讓 worker 執行緒死掉
            try:
                job = queue.claim_next()
                if job is None:
                    queue.wakeup.wait(self.poll_interval)
                    queue.wakeup.clear()
                    continue
                self.current_job = job["id"]
                try:
                    self.process(job)
                finally:
                    self.current_job = None
            except Exception:  # noqa: BLE001 - worker 不能因為單一任務或資料庫錯誤掛掉
                log.exception("worker 迴圈發生未預期錯誤，稍後重試")
                self._stop.wait(self.poll_interval)

    def process(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        try:
            self._process(job)
        except Exception:  # noqa: BLE001
            log.exception("任務 %s 發生未預期錯誤", job_id)
        finally:
            # 不管發生什麼事，任務都不能卡在 running（否則要等容器重啟才會被處理）
            try:
                current = queue.get(job_id)
                if current is not None and current["status"] == "running":
                    queue.update(job_id, status="failed", stage=STAGE_FAILED, finished_at=time.time(),
                                 error=UNEXPECTED_ERROR, cancel_requested=False)
            except Exception:  # noqa: BLE001
                log.exception("任務 %s 無法標成失敗", job_id)

    def _process(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        option = settings.model_option(job["model"])
        is_youtube = job.get("source") == "youtube"
        # 走字幕路線不需要 Whisper：要用字幕的 YouTube 任務等確定要轉錄時才檢查模型
        if option is None or (not option.available() and not youtube_source.wanted_caption(job)):
            queue.update(job_id, status="failed", stage=STAGE_FAILED, finished_at=time.time(),
                         error=msg("job.error.model_unavailable", model=job["model"]))
            return
        source = storage.original_path(job)
        # YouTube 來源的音訊由下面的下載步驟準備（第一次處理、或上次下載失敗時還沒有檔案）
        if not job["has_audio"] or (not is_youtube and not source.exists()):
            queue.update(job_id, status="failed", stage=STAGE_FAILED, finished_at=time.time(),
                         error=msg("job.error.audio_deleted"))
            return

        last_write = [0.0]

        def report(key: str, label: Message, progress: float) -> None:
            now = time.monotonic()
            if now - last_write[0] >= PROGRESS_WRITE_INTERVAL or progress >= 1.0:
                last_write[0] = now
                queue.update(job_id, stage=label, progress=round(progress, 4))

        def check_cancel() -> None:
            if queue.is_cancel_requested(job_id):
                raise Canceled()

        t0 = time.monotonic()
        reset_load_timer()

        # YouTube：先下載（進度條前面一段給下載，比例依預估的下載與處理時間）
        share = 0.0
        captions: list[dict[str, Any]] | None = None
        caption_lang: str | None = None
        prep_warnings: list[Message | str] = []
        download_seconds: float | None = None
        network_seconds = 0.0
        source_info: dict[str, Any] | None = None
        if is_youtube:
            if youtube_source.needs_download(job):
                try:
                    share = estimate.download_share(job)
                except Exception:  # noqa: BLE001 - 估算失敗不影響下載
                    share = 0.1
            try:
                prep = youtube_source.prepare(
                    job, lambda label, p: report("download", label, share * p), check_cancel,
                    max_bytes=settings.max_upload_mb * 1024 * 1024,
                )
            except Canceled:
                queue.update(job_id, status="canceled", stage=STAGE_CANCELED, finished_at=time.time(),
                             cancel_requested=False)
                return
            except YouTubeError as e:
                log.warning("任務 %s YouTube 下載失敗（%s）：%s", job_id, e.code, e.message)
                queue.update(job_id, status="failed", stage=STAGE_FAILED, finished_at=time.time(), error=e.msg,
                             cancel_requested=False)
                return
            source = prep.audio_path
            download_seconds = prep.download_seconds
            network_seconds = prep.network_seconds
            prep_warnings = list(prep.warnings)
            caption_lang = prep.caption_lang
            if prep.caption_path is not None:
                try:
                    captions = captions_mod.parse_file(prep.caption_path) or None
                except Exception as e:  # noqa: BLE001
                    log.exception("解析字幕失敗")
                    captions = None
                    prep_warnings.append(msg("youtube.captions.parse_failed", error=message_of(e)))
                if captions is None and not prep_warnings:
                    prep_warnings.append(msg("youtube.captions.empty", language=language_param(caption_lang)))
            if captions is None:
                caption_lang = None
                if not option.available():
                    # 字幕不能用（下載失敗、沒有內容），要改用模型轉錄，但模型沒安裝
                    reason = _caption_reason(prep_warnings[0]) if prep_warnings else ""
                    error = (msg("job.error.model_needed_reason", model=job["model"], reason=reason) if reason
                             else msg("job.error.model_needed", model=job["model"]))
                    queue.update(job_id, status="failed", stage=STAGE_FAILED, finished_at=time.time(),
                                 error=error, cancel_requested=False)
                    return
            job = queue.get(job_id) or job
            source_info = {"type": "youtube", "url": job.get("source_url"), "video_id": job.get("video_id"),
                           "title": job.get("yt_title"), "channel": job.get("yt_channel")}

        def report_main(key: str, label: Message, progress: float) -> None:
            report(key, label, share + (1.0 - share) * progress)

        inp = runner.JobInput(
            job_id=job_id,
            source=source,
            playback=storage.playback_path(job_id),
            model=option,
            diarize=job["diarize"],
            num_speakers=job["num_speakers"],
            dictionary=dictionary_store.load_merged(job["dictionary_ids"]),
            voiceprints=voiceprint.list_all(self.backend.embedding_model),
            captions=captions,
            caption_lang=caption_lang,
            source_info=source_info,
        )
        gpu_busy = False
        try:
            out = runner.run(inp, self.backend, report_main, check_cancel)
        except Canceled:
            queue.update(job_id, status="canceled", stage=STAGE_CANCELED, finished_at=time.time(),
                         cancel_requested=False)
            return
        except GpuBusy as e:
            queue.update(job_id, status="failed", stage=STAGE_FAILED, finished_at=time.time(), error=e.msg)
            gpu_busy = True
        except Exception as e:  # noqa: BLE001
            log.exception("任務 %s 失敗", job_id)
            # 有翻譯的錯誤（音檔無法解碼等）存訊息；其他照原本的「例外名稱: 訊息」
            error = e.msg if isinstance(e, LocalizedError) and is_message(e.msg) else f"{type(e).__name__}: {e}"[:1000]
            queue.update(job_id, status="failed", stage=STAGE_FAILED, finished_at=time.time(), error=error)
            return
        if gpu_busy:
            # 離開 except 後（traceback 已釋放）再清一次 GPU 記憶體
            free_gpu_memory()
            return

        # 實際耗時（扣掉載入模型與下載）記下來，估算之後任務的等待時間用（見 jobs/estimate.py）
        elapsed = time.monotonic() - t0
        load_seconds = min(load_timer(), elapsed)
        # 扣掉連 YouTube 的全部時間（取得資訊、字幕、音訊），不只下載音訊的時間
        dl = network_seconds
        if prep_warnings:
            out.transcript["warnings"] = prep_warnings + list(out.transcript.get("warnings") or [])
        storage.save_original(job_id, out.transcript)
        fields: dict[str, Any] = {
            "status": "done", "stage": STAGE_DONE, "progress": 1.0, "finished_at": time.time(),
            "has_transcript": True, "warnings": out.transcript.get("warnings", []),
            "duration": out.duration, "cancel_requested": False,
            "process_seconds": round(max(0.0, elapsed - load_seconds - dl), 3),
            "load_seconds": round(load_seconds, 3),
        }
        if is_youtube:
            fields["caption_source"] = f"manual:{caption_lang}" if captions else "none"
            if download_seconds is not None:
                fields["download_seconds"] = round(download_seconds, 3)
        if out.playback_created or storage.playback_path(job_id).exists():
            fields["has_playback"] = True
        queue.update(job_id, **fields)
        log.info("任務 %s 完成（%.1f 秒，其中載入模型 %.1f 秒）", job_id, elapsed, load_seconds)
