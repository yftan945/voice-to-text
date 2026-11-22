"""SQLite：資料與任務佇列都放這裡。每次操作開新連線（WAL 模式），避免跨執行緒共用連線。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    created_at REAL NOT NULL,
    queued_at REAL NOT NULL DEFAULT 0, -- 進佇列的時間（重跑時更新）；排隊順序相同時的次要排序
    position REAL NOT NULL DEFAULT 0,  -- 排隊順序：worker 依 position 由小到大領取（規則見 jobs/queue.py）
    batch_id TEXT,                     -- 同一次批次上傳的任務共用同一個值；單檔上傳為 NULL
    started_at REAL,
    finished_at REAL,
    status TEXT NOT NULL,              -- queued / running / done / failed / canceled
    stage TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL,
    diarize INTEGER NOT NULL DEFAULT 1,
    num_speakers INTEGER,
    dictionary_ids TEXT NOT NULL DEFAULT '[]',
    audio_ext TEXT NOT NULL DEFAULT '',
    has_audio INTEGER NOT NULL DEFAULT 1,
    has_playback INTEGER NOT NULL DEFAULT 0,
    has_transcript INTEGER NOT NULL DEFAULT 0,
    duration REAL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, -- 容器重啟後被重新排隊的次數（使用者重跑時歸零）
    process_seconds REAL,              -- 上次完成時的處理秒數（不含載入模型與下載），用來估算等待時間
    load_seconds REAL,                 -- 上次完成時花在載入模型的秒數
    -- 來源（2026-11-18 追加 YouTube）
    source TEXT NOT NULL DEFAULT 'file', -- file / youtube
    source_url TEXT,                   -- YouTube 影片網址（正規化後的 watch?v=）
    video_id TEXT,
    yt_title TEXT,
    yt_channel TEXT,
    yt_language TEXT,                  -- YouTube 標示的影片原始語言（選字幕語言用）
    yt_manual_captions TEXT NOT NULL DEFAULT '[]', -- 有哪些人工字幕語言（JSON）；自動字幕不算
    use_captions INTEGER NOT NULL DEFAULT 1,       -- 有人工字幕時是否直接用字幕（0＝一律用模型轉錄）
    caption_source TEXT,               -- 這次逐字稿的文字來源：manual:<語言> 或 none（模型轉錄）；尚未處理為 NULL
    download_pending INTEGER NOT NULL DEFAULT 0,   -- 還要從 YouTube 下載音訊（估算時間用）
    download_seconds REAL,             -- 上次從 YouTube 下載花的秒數
    speaker_summary TEXT               -- 講者分布摘要（JSON，見 transcript.speaker_summary）；逐字稿存檔、編輯時更新
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, queued_at);

CREATE TABLE IF NOT EXISTS transcripts (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    original TEXT NOT NULL,            -- 推論原始結果（JSON），不會被編輯
    edited TEXT,                       -- 編輯後版本（JSON）；NULL 表示沒有編輯過
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS dictionaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS voiceprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    embedding TEXT NOT NULL,           -- JSON float 陣列
    embedding_model TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',   -- upload / job:<id>
    duration REAL,
    created_at REAL NOT NULL
);
"""

_init_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(settings.db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE 交易：用在需要「讀後寫」的地方（例如領取佇列任務）。"""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()


# 舊資料庫補欄位：(資料表, 欄位, ALTER TABLE 的欄位定義)
MIGRATIONS: list[tuple[str, str, str]] = [
    ("jobs", "attempts", "attempts INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "position", "position REAL NOT NULL DEFAULT 0"),
    ("jobs", "batch_id", "batch_id TEXT"),
    ("jobs", "process_seconds", "process_seconds REAL"),
    ("jobs", "load_seconds", "load_seconds REAL"),
    ("jobs", "source", "source TEXT NOT NULL DEFAULT 'file'"),
    ("jobs", "source_url", "source_url TEXT"),
    ("jobs", "video_id", "video_id TEXT"),
    ("jobs", "yt_title", "yt_title TEXT"),
    ("jobs", "yt_channel", "yt_channel TEXT"),
    ("jobs", "yt_language", "yt_language TEXT"),
    ("jobs", "yt_manual_captions", "yt_manual_captions TEXT NOT NULL DEFAULT '[]'"),
    ("jobs", "use_captions", "use_captions INTEGER NOT NULL DEFAULT 1"),
    ("jobs", "caption_source", "caption_source TEXT"),
    ("jobs", "download_pending", "download_pending INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "download_seconds", "download_seconds REAL"),
    ("jobs", "speaker_summary", "speaker_summary TEXT"),
]

# 欄位剛補上時，在同一個交易裡跑的資料轉換
BACKFILL: dict[tuple[str, str], str] = {
    # 既有的排隊中／執行中任務依原本的 queued_at 順序編成 1..n，順序不變
    ("jobs", "position"): (
        "UPDATE jobs SET position = (SELECT COUNT(*) FROM jobs AS j2"
        " WHERE j2.status IN ('queued', 'running')"
        " AND (j2.queued_at < jobs.queued_at OR (j2.queued_at = jobs.queued_at AND j2.id <= jobs.id)))"
        " WHERE status IN ('queued', 'running')"
    ),
}

# 用到新欄位的索引：要等欄位補上之後才能建（舊資料庫執行 SCHEMA 時還沒有這些欄位）
POST_MIGRATION_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, position);"
    "CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id);"
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in MIGRATIONS:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
                if (table, column) in BACKFILL:
                    conn.execute(BACKFILL[(table, column)])
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    conn.executescript(POST_MIGRATION_SQL)


def init_db() -> None:
    with _init_lock:
        settings.ensure_dirs()
        with connect() as conn:
            conn.executescript(SCHEMA)
            _migrate(conn)
