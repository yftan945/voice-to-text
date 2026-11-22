"""檔案位置、逐字稿存取，以及三種刪除模式。"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import db
from .config import settings
from .i18n import DEFAULT_LANG, LocalizedError, render
from .jobs import queue

DELETE_MODES = ("transcript", "audio", "all")

log = logging.getLogger(__name__)

# 重新轉錄完成時會用新的原始結果取代逐字稿（含編輯，見 save_original），排隊或執行中的任務不能編輯
# zh-TW 文字（舊程式與 log 用）；給使用者看的是 i18n 的 transcript.edit.busy
EDIT_BUSY_MESSAGE = render("transcript.edit.busy", {}, DEFAULT_LANG)


class JobBusy(LocalizedError, RuntimeError):
    """任務排隊或執行中，不能編輯或刪除；msg 可以直接顯示給使用者。"""


# 補算講者摘要失敗過的任務：同一個行程裡不再重試（/api/events 每秒都會列一次，避免 log 被洗版）
_summary_failed: set[str] = set()


def _ensure_editable(conn: Any, job_id: str) -> None:
    """編輯前（同一個交易裡）確認任務不在排隊或執行中；是的話丟 JobBusy。

    檢查與寫入在同一個 BEGIN IMMEDIATE 交易裡：worker 領取任務、重新轉錄排隊也都要拿寫入鎖，不會在中間插進來。
    """
    r = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if r is not None and r["status"] in queue.ACTIVE_STATUSES:
        raise JobBusy("transcript.edit.busy")


def job_dir(job_id: str) -> Path:
    return settings.audio_dir / job_id


def original_path(job: dict[str, Any]) -> Path:
    return job_dir(job["id"]) / f"original{job.get('audio_ext') or ''}"


def playback_path(job_id: str) -> Path:
    return job_dir(job_id) / "playback.m4a"


# ---- 逐字稿 ----


def _store_summary(conn: Any, job_id: str, transcript: dict[str, Any] | None) -> dict[str, Any] | None:
    """依目前版本的逐字稿更新 jobs.speaker_summary（None＝沒有逐字稿）。和逐字稿寫入用同一個連線／交易。"""
    from .transcript import speaker_summary

    summary = speaker_summary(transcript) if transcript is not None else None
    conn.execute(
        "UPDATE jobs SET speaker_summary = ? WHERE id = ?",
        (json.dumps(summary, ensure_ascii=False) if summary is not None else None, job_id),
    )
    return summary


def save_original(job_id: str, transcript: dict[str, Any]) -> None:
    """寫入新的原始結果；重跑時舊的編輯版本一併清掉（內容已經不對應）。"""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO transcripts (job_id, original, edited, updated_at) VALUES (?, ?, NULL, ?)"
            " ON CONFLICT(job_id) DO UPDATE SET original = excluded.original, edited = NULL,"
            " updated_at = excluded.updated_at",
            (job_id, json.dumps(transcript, ensure_ascii=False), time.time()),
        )
        _store_summary(conn, job_id, transcript)


def load(job_id: str) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """回傳（原始, 編輯後或 None）。"""
    with db.connect() as conn:
        r = conn.execute("SELECT original, edited FROM transcripts WHERE job_id = ?", (job_id,)).fetchone()
    if r is None:
        return None
    return json.loads(r["original"]), (json.loads(r["edited"]) if r["edited"] else None)


T = TypeVar("T")


def edit(job_id: str, fn: Callable[[dict[str, Any], dict[str, Any], Any], T]
         ) -> tuple[dict[str, Any], dict[str, Any], T] | None:
    """在 BEGIN IMMEDIATE 交易裡讀取、修改、寫回編輯版本，兩個人同時編輯時不會互蓋。

    fn(original, current, conn) 直接修改 current（edited 沒有時是 original 的副本）；丟例外時整筆回滾。
    回傳（original, current, fn 的回傳值）；沒有逐字稿回傳 None。任務排隊或執行中（重新轉錄）丟 JobBusy。
    """
    from .transcript import make_edited

    with db.transaction() as conn:
        _ensure_editable(conn, job_id)
        r = conn.execute("SELECT original, edited FROM transcripts WHERE job_id = ?", (job_id,)).fetchone()
        if r is None:
            return None
        original = json.loads(r["original"])
        current = json.loads(r["edited"]) if r["edited"] else make_edited(original)
        result = fn(original, current, conn)
        conn.execute(
            "UPDATE transcripts SET edited = ?, updated_at = ? WHERE job_id = ?",
            (json.dumps(current, ensure_ascii=False), time.time(), job_id),
        )
        _store_summary(conn, job_id, current)
    return original, current, result


def save_edited(job_id: str, edited: dict[str, Any] | None) -> None:
    """寫入編輯版本；None＝還原成原始結果。任務排隊或執行中（重新轉錄）丟 JobBusy。"""
    with db.transaction() as conn:
        _ensure_editable(conn, job_id)
        cur = conn.execute(
            "UPDATE transcripts SET edited = ?, updated_at = ? WHERE job_id = ?",
            (json.dumps(edited, ensure_ascii=False) if edited is not None else None, time.time(), job_id),
        )
        if cur.rowcount == 0:
            return
        if edited is None:
            r = conn.execute("SELECT original FROM transcripts WHERE job_id = ?", (job_id,)).fetchone()
            edited = json.loads(r["original"])
        _store_summary(conn, job_id, edited)


def ensure_speaker_summaries(jobs: list[dict[str, Any]]) -> None:
    """舊任務（這個欄位加上之前就完成的）還沒有講者摘要：從逐字稿算一次存起來，之後列表直接讀欄位。

    直接改 jobs 裡的 dict。只處理有逐字稿、摘要還是空的任務，每筆只會算一次。
    讀取與計算在交易外做（不占寫入鎖）；寫回時加條件，期間被編輯或刪掉逐字稿的就不覆蓋。
    單筆資料壞掉（JSON 壞掉、segments 是 null…）只記 log 跳過，不讓整個列表失敗。
    """
    from .transcript import speaker_summary

    for job in jobs:
        if not job.get("has_transcript") or job.get("speaker_summary") is not None or job["id"] in _summary_failed:
            continue
        try:
            with db.connect() as conn:
                r = conn.execute("SELECT original, edited FROM transcripts WHERE job_id = ?", (job["id"],)).fetchone()
            if r is None:
                continue
            current = json.loads(r["edited"] or r["original"])
            summary = speaker_summary(current)
            with db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET speaker_summary = ? WHERE id = ? AND speaker_summary IS NULL AND has_transcript = 1",
                    (json.dumps(summary, ensure_ascii=False), job["id"]),
                )
            job["speaker_summary"] = summary
        except Exception:  # noqa: BLE001 — 一筆壞資料不能讓 /api/jobs 回 500
            _summary_failed.add(job["id"])
            log.exception("補算講者摘要失敗（任務 %s），略過", job.get("id"))


# ---- 刪除 ----


def delete_job(job_id: str, mode: str) -> dict[str, Any] | None:
    """三種刪除：
    - transcript：只刪逐字稿，保留音檔（之後可換模型／字典重跑）
    - audio：只刪音檔，保留逐字稿（結果頁無法播放、不能重跑）
    - all：整筆刪除

    回傳刪除後的任務（all 回傳 None）。任務還在排隊或執行時拒絕，避免刪到正在用的檔案。
    資料庫的變更在同一個交易裡用條件式 UPDATE／DELETE 完成（檢查 rowcount），
    不會跟 worker 領取任務或重跑互搶；檔案在交易成功後才刪。
    """
    if mode not in DELETE_MODES:
        raise ValueError(f"不支援的刪除模式：{mode}")
    idle = "id = ? AND status NOT IN ('queued', 'running')"

    with db.transaction() as conn:
        if mode == "transcript":
            cur = conn.execute(f"UPDATE jobs SET has_transcript = 0, speaker_summary = NULL WHERE {idle}", (job_id,))
        elif mode == "audio":
            cur = conn.execute(f"UPDATE jobs SET has_audio = 0, has_playback = 0 WHERE {idle}", (job_id,))
        else:
            cur = conn.execute(f"DELETE FROM jobs WHERE {idle}", (job_id,))
        if cur.rowcount == 0:
            exists = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if exists is None:
                raise KeyError(job_id)
            raise JobBusy("job.delete.busy")
        if mode in ("transcript", "all"):
            conn.execute("DELETE FROM transcripts WHERE job_id = ?", (job_id,))

    if mode in ("audio", "all"):
        shutil.rmtree(job_dir(job_id), ignore_errors=True)
    if mode == "all":
        return None
    return queue.get(job_id)
