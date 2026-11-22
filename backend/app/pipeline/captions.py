"""YouTube 人工字幕：解析 VTT／SRT，並把講者分段套到字幕段落上（純 Python，可單元測試）。

YouTube 字幕常見的問題：
- 每個 cue 重複上一個 cue 的最後一行（捲動式字幕），或連續幾個 cue 文字完全相同 → 去掉重複的行、合併相同的 cue；
  只有時間相接（下一個 cue 在上一個結束後 CONTIGUOUS_GAP 秒內開始）時才這樣處理，間隔明顯的同樣文字
  （例如兩次分開回答的「對」）照常保留成新段落；
- 文字裡有 HTML／VTT 標記（<c>、<i>、<font>、<v 講者>、行內時間 <00:00:01.000>）與 &amp; 之類的實體 → 拿掉標記、還原實體；
- 中文字幕習慣用空白當停頓 → 兩個中文字之間的空白換成「，」（與模型轉錄的輸出一致）。

輸出段落：[{start, end, text, words?}]。只有字幕本身帶逐字時間（VTT 行內時間）時才有 words；
沒有逐字時間的段落不切句，整段依重疊時間指派給一位講者。
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Sequence

from .assign import Turn, assign_speakers, speaker_for

_TS = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE_TIME_RE = re.compile(rf"^\s*{_TS}\s*-->\s*{_TS}")
_INLINE_TS_RE = re.compile(r"<(\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}>")
_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_CJK = "㐀-鿿豈-﫿"
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}])[ 　 ]+(?=[{_CJK}])")
_SRT_INDEX_RE = re.compile(r"^\d+$")
# 捲動式字幕的行重疊去重：只有時間相接（下一個 cue 的開始 ≤ 上一個 cue 的結束 + 這個秒數）才做
CONTIGUOUS_GAP = 0.05
# 文字完全相同的連續 cue 合併成一段：間隔在這個秒數以內才合併
SAME_TEXT_GAP = 0.1
# 開頭的 [音樂]、(掌聲)、♪ 這類標記只有標記沒有內容時整段略過
_ONLY_SOUND_RE = re.compile(r"^[\s♪♫]*([\[(（【][^\])）】]*[\])）】][\s♪♫]*)*$")


def _seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _clean(text: str) -> str:
    text = _INLINE_TS_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # 方向控制字元（&lrm; 等）與零寬字元
    text = re.sub(r"[​-‏‪-‮⁦-⁩﻿]", "", text)
    return re.sub(r"[ \t ]+", " ", text).strip()


def _join_lines(lines: list[str]) -> str:
    # 多行用空白接起來；中文接中文的空白在 _finish_text 換成「，」
    return " ".join(ln for ln in lines if ln)


def _is_cjk(ch: str) -> bool:
    return bool(ch) and re.match(rf"[{_CJK}]", ch) is not None


def _finish_text(text: str) -> str:
    text = _CJK_SPACE_RE.sub("，", text)
    return re.sub(r"\s+", " ", text).strip()


def _inline_words(raw_lines: list[str], start: float, end: float) -> list[dict[str, Any]] | None:
    """VTT 行內時間（<00:00:01.000>）→ 逐字時間；沒有行內時間回傳 None。"""
    raw = " ".join(raw_lines)
    if not _INLINE_TS_RE.search(raw):
        return None
    words: list[dict[str, Any]] = []
    pos = 0
    t = start
    pieces: list[tuple[float, str]] = []
    for m in _INLINE_TS_RE.finditer(raw):
        pieces.append((t, raw[pos:m.start()]))
        ts = m.group(0)[1:-1]
        mt = re.match(_TS, ts)
        if mt:
            t = _seconds(*mt.groups())
        pos = m.end()
    pieces.append((t, raw[pos:]))
    space_before = False  # 上一塊結尾（或中間被略過的空白塊）有沒有空白
    for i, (t0, chunk) in enumerate(pieces):
        # 去掉標記但保留前後空白，用來判斷塊與塊之間原本有沒有停頓
        bare = _TAG_RE.sub("", chunk)
        text = _finish_text(_clean(chunk))
        if not text:
            space_before = space_before or (bool(bare) and bare.isspace())
            continue
        t1 = pieces[i + 1][0] if i + 1 < len(pieces) else end
        gap = space_before or bare[:1].isspace()
        if not words:
            sep = ""
        elif _is_cjk(words[-1]["text"][-1]) and _is_cjk(text[0]):
            # 中文接中文：原本有空白（停頓）時跟整段文字一樣換成「，」，沒有就直接接
            sep = "，" if gap else ""
        else:
            sep = " "  # 英文單字之間補空白
        words.append({"text": sep + text, "start": t0, "end": max(t0, t1)})
        space_before = bare[-1:].isspace()
    return words or None


def _cues_vtt(text: str) -> list[tuple[float, float, list[str]]]:
    cues: list[tuple[float, float, list[str]]] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿"))
    for block in blocks:
        lines = block.split("\n")
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue  # WEBVTT 標頭、NOTE、STYLE、REGION
        if lines[0].strip().startswith(("NOTE", "STYLE", "REGION")) and idx > 0:
            continue
        m = _CUE_TIME_RE.match(lines[idx])
        if not m:
            continue
        g = m.groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        cues.append((start, end, [ln for ln in lines[idx + 1:]]))
    return cues


def _cues_srt(text: str) -> list[tuple[float, float, list[str]]]:
    """SRT 不用空白行切 cue（cue 文字裡可能有空白行）：以時間行為準，
    一個 cue 的文字是時間行之後、到下一個「序號＋時間行」之前的所有行。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").split("\n")
    times = [i for i, ln in enumerate(lines) if _CUE_TIME_RE.match(ln)]
    cues: list[tuple[float, float, list[str]]] = []
    for n, i in enumerate(times):
        stop = times[n + 1] if n + 1 < len(times) else len(lines)
        body = lines[i + 1:stop]
        # 下一個 cue 的序號（緊接在下一個時間行前面）不算這個 cue 的文字
        if n + 1 < len(times) and body and _SRT_INDEX_RE.match(body[-1].strip()):
            body = body[:-1]
        while body and not body[-1].strip():
            body.pop()
        m = _CUE_TIME_RE.match(lines[i])
        assert m is not None
        g = m.groups()
        cues.append((_seconds(*g[:4]), _seconds(*g[4:]), body))
    return cues


def parse(text: str, fmt: str) -> list[dict[str, Any]]:
    """解析字幕檔內容（fmt：vtt／srt），回傳去重後的段落。"""
    fmt = fmt.lower().lstrip(".")
    raw = _cues_vtt(text) if fmt == "vtt" else _cues_srt(text)
    raw.sort(key=lambda c: c[0])

    segments: list[dict[str, Any]] = []
    prev_lines: list[str] = []
    prev_end = float("-inf")
    for start, end, raw_lines in raw:
        lines = [_clean(ln) for ln in raw_lines]
        lines = [ln for ln in lines if ln]
        words = _inline_words(raw_lines, start, end) if fmt == "vtt" else None
        # 捲動式字幕：時間相接，而且這個 cue 開頭幾行跟上一個 cue 結尾幾行相同 → 去掉重複的部分
        overlap = 0
        if start <= prev_end + CONTIGUOUS_GAP:
            for k in range(min(len(lines), len(prev_lines)), 0, -1):
                if lines[:k] == prev_lines[-k:]:
                    overlap = k
                    break
        new_lines = lines[overlap:]
        if lines:
            prev_lines = lines
            prev_end = end
        if not new_lines:
            # 整個 cue 都是重複的（一定時間相接）：只延長上一段的結束時間
            if segments and start <= segments[-1]["end"] + CONTIGUOUS_GAP:
                segments[-1]["end"] = max(segments[-1]["end"], end)
            continue
        if overlap and words:
            words = None  # 重複行的逐字時間對不上，這種 cue 不用逐字時間
        text_out = _finish_text(_join_lines(new_lines))
        if not text_out or _ONLY_SOUND_RE.match(text_out):
            continue
        if end - start < 0.05 and segments and segments[-1]["text"].endswith(text_out):
            continue  # YouTube 常見的 10 毫秒「快照」cue
        last = segments[-1] if segments else None
        if last is not None and last["text"] == text_out and start <= last["end"] + SAME_TEXT_GAP:
            last["end"] = max(last["end"], end)
            continue
        seg: dict[str, Any] = {"start": round(start, 3), "end": round(max(end, start), 3), "text": text_out}
        if words:
            seg["words"] = words
        segments.append(seg)
    return segments


def parse_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    fmt = path.suffix.lstrip(".").lower()
    if fmt not in ("vtt", "srt"):
        fmt = "vtt" if text.lstrip("﻿").startswith("WEBVTT") else "srt"
    return parse(text, fmt)


# ---- 講者 ----


def majority_speaker(start: float, end: float, turns: Sequence[Turn]) -> str | None:
    """重疊時間加總最多的講者（同一人可能有好幾段）；完全沒重疊時退回 speaker_for 的最近講者。"""
    total: dict[str, float] = {}
    for t0, t1, spk in turns:
        ov = min(end, t1) - max(start, t0)
        if ov > 0:
            total[spk] = total.get(spk, 0.0) + ov
    if total:
        return max(total.items(), key=lambda kv: kv[1])[0]
    return speaker_for(start, end, turns)


def assign_caption_speakers(segments: Sequence[dict[str, Any]], turns: Sequence[Turn] | None
                            ) -> list[dict[str, Any]]:
    """字幕段落指派講者：有逐字時間的段落照模型轉錄的規則在換講者處切句；
    沒有逐字時間的段落不切句，整段給重疊時間最多的講者。"""
    turns = sorted(turns or [], key=lambda t: t[0])
    out: list[dict[str, Any]] = []
    last: str | None = None
    for seg in segments:
        start, end = float(seg["start"]), float(seg["end"])
        if seg.get("words") and turns:
            parts = assign_speakers([seg], turns)
            for p in parts:
                if p.get("speaker") is None:
                    p["speaker"] = last
                out.append({k: p[k] for k in ("start", "end", "speaker", "text")})
            if parts and parts[-1].get("speaker"):
                last = parts[-1]["speaker"]
            continue
        spk = majority_speaker(start, end, turns) if turns else None
        if spk is None and turns:
            spk = last
        out.append({"start": start, "end": end, "speaker": spk, "text": seg["text"]})
        if spk:
            last = spk
    # 開頭幾則沒對到任何講者（也沒有上一則可沿用）：沿用之後第一個有講者的字幕
    if turns:
        first = next((s["speaker"] for s in out if s["speaker"]), None)
        for seg in out:
            if seg["speaker"]:
                break
            seg["speaker"] = first
    for i, seg in enumerate(out):
        seg["id"] = i
    return out
