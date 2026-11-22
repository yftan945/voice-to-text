"""用子行程執行 yt-dlp：取得影片／播放清單資訊、下載音訊與字幕（含進度、取消、逾時）。

為什麼用子行程而不是在 worker 執行緒裡 import yt_dlp：
- 取消或卡住（網路掛住）時可以直接砍掉整個行程群組（含 yt-dlp 叫起來的 deno），worker 不會被卡住；
- yt-dlp 要常常更新，獨立行程不會跟推論環境的套件互相影響。

只傳我們自己組出來的 youtube.com 網址（見 app/youtube.py），並限制只用 YouTube 的 extractor。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..youtube import YouTubeError, classify_error, playlist_url, video_url

log = logging.getLogger(__name__)

YTDLP = [sys.executable, "-m", "yt_dlp"]
COMMON_ARGS = [
    "--ignore-config",       # 不讀容器裡任何 yt-dlp 設定檔
    "--no-colors",
    "--socket-timeout", "30",
    "--retries", "3",
    "--fragment-retries", "3",
]
PROGRESS_PREFIX = "V2TPROG"
PROGRESS_TEMPLATE = (
    f"download:{PROGRESS_PREFIX} %(progress.downloaded_bytes)s %(progress.total_bytes)s "
    "%(progress.total_bytes_estimate)s %(progress.status)s"
)
INFO_TIMEOUT = 90        # 取得影片資訊
PLAYLIST_TIMEOUT = 120   # 展開播放清單
PLAYLIST_LIMIT = 200     # 播放清單最多列出幾支
MIN_DOWNLOAD_TIMEOUT = 600
# worker 連 YouTube 時每個請求之間停多久（秒）：批次任務是一支接一支連續下載，拉開間隔降低被限流（429）的機會。
# --sleep-requests 作用在解析（取得資訊、--load-info-json 失敗時 yt-dlp 改用網址重新解析）的請求，
# --sleep-subtitles 作用在下載字幕前。API 的 /youtube/resolve 要即時回應，不加。
SLEEP_REQUESTS = 1
SLEEP_SUBTITLES = 1

# 子行程輸出的字元：yt-dlp 在沒有終端機時預設用 locale，強制 UTF-8 避免標題亂碼
_ENV_OVERRIDES = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

Runner = Callable[..., "Result"]


class Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run(args: list[str], *, timeout: float, check_cancel: Callable[[], None] | None = None,
        on_line: Callable[[str], None] | None = None) -> Result:
    """執行 yt-dlp。check_cancel 丟例外（Canceled）時砍掉行程再往上丟；超過 timeout 丟 YouTubeError。

    on_line 收到 stdout 的每一行（下載進度用）；沒給時 stdout 全部收集起來（-J 的 JSON）。
    """
    env = {**os.environ, **_ENV_OVERRIDES}
    try:
        proc = subprocess.Popen(
            [*YTDLP, *COMMON_ARGS, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env,
            start_new_session=True,  # 自己一個行程群組：取消時連 deno 一起砍
        )
    except FileNotFoundError as e:
        raise YouTubeError("missing", "伺服器沒有安裝 yt-dlp", 502) from e
    out: list[str] = []
    err: list[str] = []

    def read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if on_line is not None:
                try:
                    on_line(line.rstrip("\n"))
                except Exception:  # noqa: BLE001 - 進度回報失敗不影響下載
                    log.exception("處理 yt-dlp 輸出失敗")
            else:
                out.append(line)

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err.append(line)
            if len(err) > 400:
                del err[:200]

    threads = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                pass
            if check_cancel is not None:
                check_cancel()
            if time.monotonic() > deadline:
                _kill(proc)
                proc.wait()
                raise YouTubeError("timeout", "連線 YouTube 逾時，請稍後再試。", 502)
    except BaseException:
        if proc.poll() is None:
            _kill(proc)
            proc.wait()
        raise
    finally:
        for t in threads:
            t.join(timeout=5)
    return Result(proc.returncode, "".join(out), "".join(err))


def _json_or_error(res: Result) -> dict[str, Any]:
    if res.returncode != 0:
        raise classify_error(res.stderr)
    try:
        data = json.loads(res.stdout)
    except ValueError as e:
        raise classify_error(res.stderr or "無法解析 yt-dlp 的輸出") from e
    if not isinstance(data, dict):
        raise YouTubeError("unknown", "YouTube 回傳的資料格式不對", 502)
    return data


def fetch_video_info(video_id: str, *, check_cancel: Callable[[], None] | None = None,
                     timeout: float = INFO_TIMEOUT, sleep_requests: float | None = None) -> dict[str, Any]:
    """單一影片的完整資訊（yt-dlp -J），含人工字幕（subtitles）與自動字幕（automatic_captions）。

    sleep_requests：解析時每個請求之間停幾秒（worker 用；API 不加）。
    """
    args = ["--no-playlist", "--use-extractors", "youtube"]
    if sleep_requests:
        args += ["--sleep-requests", str(sleep_requests)]
    res = run([*args, "-J", video_url(video_id)], timeout=timeout, check_cancel=check_cancel)
    return _json_or_error(res)


def fetch_playlist(playlist_id: str, *, limit: int = PLAYLIST_LIMIT,
                   timeout: float = PLAYLIST_TIMEOUT) -> dict[str, Any]:
    """播放清單（只列影片，不逐支解析）。"""
    res = run(["--flat-playlist", "--playlist-end", str(limit), "--use-extractors", "youtube:tab", "-J",
               playlist_url(playlist_id)], timeout=timeout)
    return _json_or_error(res)


def parse_progress(line: str) -> tuple[float, float | None] | None:
    """解析進度行，回傳（已下載 bytes, 總大小或 None）。"""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    parts = line.split()
    if len(parts) < 4:
        return None

    def num(s: str) -> float | None:
        try:
            v = float(s)
        except ValueError:
            return None
        return v if v > 0 else None

    done = num(parts[1]) or 0.0
    total = num(parts[2]) or num(parts[3])
    return done, total


def download(info: dict[str, Any], dest_dir: Path, *, audio: bool, caption_lang: str | None,
             on_progress: Callable[[float], None], check_cancel: Callable[[], None] | None,
             max_bytes: int, duration: float | None = None) -> None:
    """用已取得的影片資訊（--load-info-json，不再重新解析）下載音訊或指定語言的人工字幕。

    音訊存成 dest_dir/original.<ext>（最佳音質的純音訊，之後由 ffmpeg 轉成 WAV 與播放用 m4a），
    字幕存成 dest_dir/captions.<lang>.<ext>（vtt 優先，其次 srt）。失敗丟 YouTubeError。
    youtube_source.prepare 把字幕與音訊分成兩次呼叫：字幕失敗（例如 429）只影響字幕，不會讓整個任務失敗。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    info_path = dest_dir / "youtube.info.json"
    tmp = info_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    tmp.replace(info_path)
    # --use-extractors：資訊檔失效時 yt-dlp 會改用 webpage_url 重新解析，同樣只允許 YouTube（縱深防禦）
    args = ["--load-info-json", str(info_path), "--use-extractors", "youtube",
            "--sleep-requests", str(SLEEP_REQUESTS), "--no-mtime", "--newline", "-q", "--progress",
            "--progress-template", PROGRESS_TEMPLATE, "-P", str(dest_dir),
            "-o", "original.%(ext)s", "-o", "subtitle:captions.%(ext)s"]
    if audio:
        args += ["-f", "bestaudio/best", "--max-filesize", str(max_bytes)]
    else:
        args += ["--skip-download"]
    if caption_lang:
        # 只要人工字幕（--write-subs）；自動字幕（--write-auto-subs）不下載
        args += ["--write-subs", "--sub-langs", caption_lang, "--sub-format", "vtt/srt/best",
                 "--sleep-subtitles", str(SLEEP_SUBTITLES)]

    def on_line(line: str) -> None:
        p = parse_progress(line)
        if p is None:
            return
        done, total = p
        if total:
            on_progress(min(1.0, done / total))

    timeout = max(MIN_DOWNLOAD_TIMEOUT, (duration or 0) * 2)
    try:
        res = run(args, timeout=timeout, check_cancel=check_cancel, on_line=on_line)
    except BaseException:
        cleanup_partial(dest_dir)
        raise
    finally:
        info_path.unlink(missing_ok=True)
    if res.returncode != 0:
        cleanup_partial(dest_dir)
        raise classify_error(res.stderr)
    if audio and find_audio(dest_dir) is None:
        # --max-filesize 超過時 yt-dlp 只印訊息、回傳 0，不會產生檔案
        if "max-filesize" in res.stderr.lower() or "larger than" in res.stderr.lower():
            raise classify_error("ERROR: File is larger than max-filesize")
        raise classify_error(res.stderr or "ERROR: 下載後找不到音訊檔")
    on_progress(1.0)


def cleanup_partial(dest_dir: Path) -> None:
    """取消或失敗時清掉下載到一半的檔案（只清 yt-dlp 的暫存檔與資訊檔，不動已完成的音訊）。"""
    for p in dest_dir.glob("*"):
        name = p.name
        if name.endswith((".part", ".ytdl", ".tmp")) or ".part-Frag" in name or name.startswith("youtube.info"):
            p.unlink(missing_ok=True)


_SKIP_SUFFIXES = (".part", ".ytdl", ".tmp", ".json")


def find_audio(dest_dir: Path) -> Path | None:
    for p in sorted(dest_dir.glob("original.*")):
        if p.is_file() and not p.name.endswith(_SKIP_SUFFIXES) and ".part" not in p.name and p.stat().st_size > 0:
            return p
    return None


def find_captions(dest_dir: Path, lang: str) -> Path | None:
    for ext in ("vtt", "srt"):
        p = dest_dir / f"captions.{lang}.{ext}"
        if p.is_file() and p.stat().st_size > 0:
            return p
    for p in sorted(dest_dir.glob(f"captions.{lang}.*")):
        if p.is_file() and p.stat().st_size > 0 and not p.name.endswith(_SKIP_SUFFIXES):
            return p
    return None


_version_cache: list[str] = []
_version_failed_at = [0.0]
VERSION_RETRY_SECONDS = 60  # 查不到版本時，隔多久再試（不永久記住失敗，例如剛好逾時）


def version() -> str | None:
    """yt-dlp 版本（/api/status 顯示用）；沒安裝或查詢失敗回傳 None（之後會再試）。"""
    if _version_cache:
        return _version_cache[0]
    if _version_failed_at[0] and time.monotonic() - _version_failed_at[0] < VERSION_RETRY_SECONDS:
        return None
    try:
        proc = subprocess.run([*YTDLP, "--version"], capture_output=True, text=True, timeout=30, check=False)
        v = proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        v = ""
    if not v:
        _version_failed_at[0] = time.monotonic()
        return None
    _version_cache.append(v)
    return v
