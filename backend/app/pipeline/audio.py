"""ffmpeg 轉檔。

iPhone 語音備忘錄 `.qta` 有兩條音軌：AAC 立體聲＋Apple APAC 4 聲道。ffmpeg 不支援 APAC，
但預設會挑聲道多的那條，所以一定要明確用 `-map 0:a:<n>` 選音軌，並從第一條開始逐條嘗試，
選第一條能解碼的。
"""
from __future__ import annotations

import json
import logging
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# 只允許讀本機檔案：防止上傳的播放清單（hls／concat 等）讓 ffmpeg 連到內網或其他網址（SSRF）
PROTOCOL_WHITELIST = ["-protocol_whitelist", "file,pipe"]
# 會去讀其他檔案或網址的容器格式，一律拒絕
BLOCKED_FORMATS = frozenset({
    "hls", "applehttp", "concat", "ffconcat", "dash", "rtsp", "rtp", "sdp", "tee", "image2", "mpjpeg",
})


class AudioError(RuntimeError):
    pass


@dataclass
class ProbeResult:
    duration: float | None
    audio_streams: int
    format_name: str | None = None


def probe(path: Path) -> ProbeResult:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", *PROTOCOL_WHITELIST, "-print_format", "json",
                "-show_format", "-show_streams", "-select_streams", "a", str(path),
            ],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except FileNotFoundError as e:
        raise AudioError("找不到 ffprobe") from e
    if proc.returncode != 0:
        raise AudioError(f"無法讀取音檔：{proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    format_name = fmt.get("format_name") or None
    if format_name and BLOCKED_FORMATS.intersection(format_name.split(",")):
        raise AudioError(f"不支援這種檔案格式（{format_name}），請上傳一般的音訊或影片檔")
    dur = fmt.get("duration")
    duration = float(dur) if dur not in (None, "N/A") else None
    if duration is None:
        for s in streams:
            if s.get("duration") not in (None, "N/A"):
                duration = float(s["duration"])
                break
    return ProbeResult(duration=duration, audio_streams=len(streams), format_name=format_name)


def _run_ffmpeg(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *PROTOCOL_WHITELIST, *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as e:
        raise AudioError("找不到 ffmpeg") from e


def convert_to_wav(src: Path, dst: Path, num_streams: int | None = None) -> int:
    """轉成 16kHz 單聲道 WAV，回傳實際使用的音軌編號。"""
    if num_streams is None:
        num_streams = probe(src).audio_streams
    if num_streams <= 0:
        raise AudioError("檔案裡沒有音軌")
    last_err = ""
    for idx in range(num_streams):
        proc = _run_ffmpeg(
            ["-i", str(src), "-map", f"0:a:{idx}", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-c:a", "pcm_s16le", str(dst)]
        )
        if proc.returncode == 0 and dst.exists() and dst.stat().st_size > 44:
            if idx > 0:
                log.info("第 %d 條音軌無法解碼，改用第 %d 條", idx - 1, idx)
            return idx
        last_err = proc.stderr.strip()[-300:]
        log.warning("音軌 0:a:%d 轉檔失敗：%s", idx, last_err)
    raise AudioError(f"所有音軌都無法解碼：{last_err}")


def make_playback(src: Path, dst: Path, stream_index: int) -> None:
    """產生瀏覽器（含 iOS Safari）都能播放、可拖曳跳播的 AAC m4a。"""
    tmp = dst.with_suffix(".tmp.m4a")
    proc = _run_ffmpeg(
        ["-i", str(src), "-map", f"0:a:{stream_index}", "-vn", "-ac", "1", "-c:a", "aac",
         "-b:a", "64k", "-movflags", "+faststart", str(tmp)]
    )
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise AudioError(f"產生播放檔失敗：{proc.stderr.strip()[-300:]}")
    tmp.replace(dst)


def load_wav(path: Path):
    """讀 16-bit PCM WAV 成 float32 numpy 陣列（-1 ~ 1）。"""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise AudioError("只支援 16-bit WAV")
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data
