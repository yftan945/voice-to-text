"""任務資料與 SQLite 佇列。

排隊順序（`position` 欄位，數字小的先處理；相同時依 queued_at、id）：
- 新上傳：排在目前佇列的最後（排隊中最大的 position + 1）。
- 重新轉錄：同新上傳，排到最後。
- 容器重啟後放回佇列的任務（上次處理到一半）：排在最前面（排隊中最小的 position - 1）。
- 使用者調整順序（`move`）：在 BEGIN IMMEDIATE 交易裡把排隊中的任務依新順序重新編號為 1..n。
- 處理中的任務不在排隊順序裡，不能移動；其他任務也不能排到它前面。
- 修改排隊中任務的設定（`update_queued_options`）不動 position／queued_at，順序不變。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any

from .. import db

ACTIVE_STATUSES = ("queued", "running")

# 排隊中任務的領取順序
QUEUE_ORDER = "position, queued_at, id"
# 新任務／重跑：排到最後
_NEXT_POSITION = "(SELECT COALESCE(MAX(position), 0) + 1 FROM jobs WHERE status = 'queued')"
# 容器重啟後重新排隊：排到最前
_FIRST_POSITION = "(SELECT COALESCE(MIN(position), 1) - 1 FROM jobs WHERE status = 'queued')"

BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 有新任務進佇列時叫醒 worker
wakeup = threading.Event()


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["warnings"] = json.loads(d.get("warnings") or "[]")
    d["dictionary_ids"] = json.loads(d.get("dictionary_ids") or "[]")
    d["yt_manual_captions"] = json.loads(d.get("yt_manual_captions") or "[]")
    d["speaker_summary"] = json.loads(d["speaker_summary"]) if d.get("speaker_summary") else None
    for k in ("diarize", "has_audio", "has_playback", "has_transcript", "cancel_requested", "use_captions",
              "download_pending"):
        d[k] = bool(d[k])
    return d


JSON_FIELDS = ("warnings", "dictionary_ids", "yt_manual_captions", "speaker_summary")


def create(*, filename: str, model: str, diarize: bool, num_speakers: int | None,
           dictionary_ids: list[int], audio_ext: str, size_bytes: int, job_id: str | None = None,
           batch_id: str | None = None, youtube: dict[str, Any] | None = None,
           duration: float | None = None) -> dict[str, Any]:
    """建立任務並排到佇列最後。youtube 有值時是 YouTube 來源（音訊由 worker 下載）：
    {video_id, source_url, title, channel, language, manual_captions, use_captions}。"""
    job_id = job_id or uuid.uuid4().hex
    now = time.time()
    yt = youtube or {}
    with db.connect() as conn:
        # 單一 INSERT 敘述：取最大 position 與寫入在同一個寫入鎖內完成，同時上傳也不會拿到同一個順位
        conn.execute(
            "INSERT INTO jobs (id, filename, created_at, queued_at, position, batch_id, status, stage, progress,"
            " model, diarize, num_speakers, dictionary_ids, audio_ext, size_bytes, has_audio, duration,"
            " source, source_url, video_id, yt_title, yt_channel, yt_language, yt_manual_captions, use_captions,"
            " download_pending)"
            f" VALUES (?, ?, ?, ?, {_NEXT_POSITION}, ?, 'queued', '排隊中', 0, ?, ?, ?, ?, ?, ?, 1, ?,"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, filename, now, now, batch_id, model, int(diarize), num_speakers,
             json.dumps(dictionary_ids), audio_ext, size_bytes, duration,
             "youtube" if youtube else "file", yt.get("source_url"), yt.get("video_id"), yt.get("title"),
             yt.get("channel"), yt.get("language"), json.dumps(yt.get("manual_captions") or []),
             int(bool(yt.get("use_captions", True))), int(bool(youtube))),
        )
    wakeup.set()
    return get(job_id)  # type: ignore[return-value]


def new_id() -> str:
    return uuid.uuid4().hex


def get(job_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row(r) if r else None


def list_all() -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def list_active() -> list[dict[str, Any]]:
    """處理中的任務在前，接著是排隊中的任務（依領取順序）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'running')"
            f" ORDER BY status = 'queued', {QUEUE_ORDER}"
        ).fetchall()
    return [_row(r) for r in rows]


def list_batch(batch_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at, id", (batch_id,)).fetchall()
    return [_row(r) for r in rows]


def update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k in JSON_FIELDS:
            v = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            v = int(v)
        cols.append(f"{k} = ?")
        vals.append(v)
    vals.append(job_id)
    with db.connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id = ?", vals)


def claim_next() -> dict[str, Any] | None:
    """依排隊順序（position）領取下一個任務並標成 running（交易內完成，不會重複領取）。"""
    with db.transaction() as conn:
        r = conn.execute(
            f"SELECT id FROM jobs WHERE status = 'queued' ORDER BY {QUEUE_ORDER} LIMIT 1"
        ).fetchone()
        if r is None:
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, finished_at = NULL, stage = '準備中',"
            " progress = 0, error = NULL WHERE id = ?",
            (time.time(), r["id"]),
        )
        job_id = r["id"]
    return get(job_id)


# 容器重啟後最多重新排隊幾次；再中斷就標為失敗，避免壞檔讓程式一直崩潰重來
MAX_RESTART_REQUEUES = 2
RESTART_FAILED_MESSAGE = "處理時程式中斷多次，可能是檔案過長或損壞"


def requeue_unfinished() -> int:
    """容器重啟後，把上次跑到一半的任務放回佇列；已經重新排隊超過上限的標為失敗。回傳重新排隊的數量。"""
    now = time.time()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', stage = '失敗', finished_at = ?, error = ?,"
            " cancel_requested = 0 WHERE status = 'running' AND attempts >= ?",
            (now, RESTART_FAILED_MESSAGE, MAX_RESTART_REQUEUES),
        )
        # 上次處理到一半的任務原本就在最前面，放回佇列時排在最前
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued', stage = '排隊中（重新啟動後重新排隊）', progress = 0,"
            f" attempts = attempts + 1, position = {_FIRST_POSITION} WHERE status = 'running'"
        )
        n = cur.rowcount
    if n:
        wakeup.set()
    return n


def requeue(job_id: str, *, model: str, diarize: bool, num_speakers: int | None,
            dictionary_ids: list[int], use_captions: bool | None = None) -> bool:
    """重跑：換模型／字典後重新排隊（排到佇列最後）。舊的逐字稿在完成後才會被新的覆蓋。

    條件式 UPDATE：只有不在排隊／執行中、且音檔還在的任務才會被重新排隊（避免與 worker 或刪除互搶）。
    batch_id 保留不變（仍算原本那一批）。use_captions 為 None 時沿用原本的設定（YouTube 來源才有意義）。
    回傳是否成功。
    """
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued', stage = '排隊中', progress = 0, error = NULL,"
            " warnings = '[]', model = ?, diarize = ?, num_speakers = ?, dictionary_ids = ?,"
            " use_captions = COALESCE(?, use_captions),"
            " cancel_requested = 0, attempts = 0, queued_at = ?, started_at = NULL, finished_at = NULL,"
            f" position = {_NEXT_POSITION}"
            " WHERE id = ? AND status NOT IN ('queued', 'running') AND has_audio = 1",
            (model, int(diarize), num_speakers, json.dumps(dictionary_ids),
             None if use_captions is None else int(use_captions), time.time(), job_id),
        )
    if cur.rowcount:
        wakeup.set()
    return cur.rowcount > 0


class NotQueuedError(Exception):
    """任務已經不在排隊中（開始處理或已結束），不能修改設定；訊息可以直接顯示給使用者。"""


# 排隊中可以修改的設定欄位（不含 position、batch_id、queued_at：修改設定不影響排隊順序與批次）
QUEUED_OPTION_FIELDS = ("model", "diarize", "num_speakers", "dictionary_ids", "use_captions")


def update_queued_options(job_id: str, **fields: Any) -> dict[str, Any] | None:
    """修改排隊中（還沒開始）任務的設定，回傳更新後的任務。只改這一筆，同批其他任務不受影響。

    在 BEGIN IMMEDIATE 交易裡用條件式 UPDATE（status = 'queued'），跟 worker 的 claim_next 互斥：
    worker 先領走的話這裡影響 0 列，不會改到已經開始處理的任務。
    fields 只能是 QUEUED_OPTION_FIELDS；沒有欄位時只確認任務仍在排隊中。
    找不到任務丟 KeyError；任務不在排隊中丟 NotQueuedError。
    """
    bad = set(fields) - set(QUEUED_OPTION_FIELDS)
    if bad:
        raise ValueError(f"不能修改的欄位：{', '.join(sorted(bad))}")
    cols = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k in JSON_FIELDS:
            v = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            v = int(v)
        cols.append(f"{k} = ?")
        vals.append(v)
    with db.transaction() as conn:
        if cols:
            n = conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id = ? AND status = 'queued'",
                             (*vals, job_id)).rowcount
        else:
            n = conn.execute("SELECT COUNT(*) FROM jobs WHERE id = ? AND status = 'queued'",
                             (job_id,)).fetchone()[0]
        if not n:
            r = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if r is None:
                raise KeyError(job_id)
            if r["status"] == "running":
                raise NotQueuedError("任務已經開始處理，無法修改設定")
            raise NotQueuedError("任務已經結束，無法修改設定")
    # 交易結束後才讀：這中間被刪除時回傳 None
    return get(job_id)


def queue_position(job_id: str) -> int | None:
    """前面還有幾個任務（含執行中）。"""
    with db.connect() as conn:
        r = conn.execute("SELECT status, position, queued_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None or r["status"] != "queued":
            return None
        n = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'running'"
            " OR (status = 'queued' AND (position, queued_at, id) < (?, ?, ?))",
            (r["position"], r["queued_at"], job_id),
        ).fetchone()[0]
    return int(n)


class MoveError(Exception):
    """不能調整順序（任務不在排隊中、目標任務已經開始處理等），訊息可以直接顯示給使用者。"""


MOVE_DIRECTIONS = ("front", "back", "up", "down")


def move(job_id: str, *, to: str | None = None, before: str | None = None,
         after: str | None = None) -> list[str]:
    """調整排隊順序，回傳調整後排隊中任務的 id（領取順序）。

    三種指定方式擇一：`to`（front／back／up／down）、`before`（排到某個任務前面）、`after`（排到某個任務後面）。
    整個操作在 BEGIN IMMEDIATE 交易裡完成：讀出目前順序、搬移、重新編號為 1..n，
    跟 worker 領取任務、其他人同時調整順序、新上傳都不會互相蓋掉。
    找不到任務丟 KeyError；任務不在排隊中、或目標任務已不在排隊中丟 MoveError。
    """
    if sum(x is not None for x in (to, before, after)) != 1:
        raise ValueError("to、before、after 要剛好指定一個")
    if to is not None and to not in MOVE_DIRECTIONS:
        raise ValueError(f"不支援的方向：{to}")
    with db.transaction() as conn:
        r = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None:
            raise KeyError(job_id)
        if r["status"] == "running":
            raise MoveError("處理中的任務不能移動")
        if r["status"] != "queued":
            raise MoveError("只有排隊中的任務可以調整順序")
        ids = [x["id"] for x in conn.execute(
            f"SELECT id FROM jobs WHERE status = 'queued' ORDER BY {QUEUE_ORDER}").fetchall()]
        i = ids.index(job_id)
        ids.pop(i)
        if to == "front":
            j = 0
        elif to == "back":
            j = len(ids)
        elif to == "up":
            j = max(0, i - 1)
        elif to == "down":
            j = min(len(ids), i + 1)
        else:
            target = before if before is not None else after
            if target == job_id:
                j = i
            elif target not in ids:
                raise MoveError("目標任務已經開始處理或不在佇列中，請重新整理後再試")
            else:
                j = ids.index(target) + (1 if after is not None else 0)
        ids.insert(j, job_id)
        conn.executemany("UPDATE jobs SET position = ? WHERE id = ?", [(n + 1, x) for n, x in enumerate(ids)])
    return ids


def request_cancel(job_id: str) -> str | None:
    """取消：排隊中直接標成 canceled；執行中設旗標，由 worker 在下一個檢查點停止。"""
    with db.transaction() as conn:
        r = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None:
            return None
        if r["status"] == "queued":
            conn.execute(
                "UPDATE jobs SET status = 'canceled', stage = '已取消', finished_at = ? WHERE id = ?",
                (time.time(), job_id),
            )
            return "canceled"
        if r["status"] == "running":
            conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
            return "canceling"
        return r["status"]


def is_cancel_requested(job_id: str) -> bool:
    with db.connect() as conn:
        r = conn.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(r and r["cancel_requested"])


def cancel_batch(batch_id: str) -> dict[str, int] | None:
    """整批取消：排隊中的直接取消、執行中的設旗標（同一個交易）。已結束的任務不動。

    回傳 {"canceled": 直接取消數, "canceling": 等 worker 停止數, "total": 這批任務總數}；批次不存在回傳 None。
    """
    now = time.time()
    with db.transaction() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs WHERE batch_id = ?", (batch_id,)).fetchone()[0]
        if not total:
            return None
        canceled = conn.execute(
            "UPDATE jobs SET status = 'canceled', stage = '已取消', finished_at = ?"
            " WHERE batch_id = ? AND status = 'queued'",
            (now, batch_id),
        ).rowcount
        canceling = conn.execute(
            "UPDATE jobs SET cancel_requested = 1 WHERE batch_id = ? AND status = 'running'",
            (batch_id,),
        ).rowcount
    return {"canceled": canceled, "canceling": canceling, "total": int(total)}
