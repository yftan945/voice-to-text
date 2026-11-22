"""WhisperX 逐字時間對齊（中文以字為單位）。"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

log = logging.getLogger(__name__)

NO_SPACE_LANGUAGES = {"zh", "ja", "th", "lo", "my", "km", "yue"}


def torch_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def load_aligner(language: str):
    from whisperx.alignment import load_align_model

    model, metadata = load_align_model(language_code=language, device=torch_device())
    return model, metadata


def align(aligner, segments: list[dict[str, Any]], audio, language: str) -> list[dict[str, Any]]:
    """回傳 [{start, end, text, words: [{text, start, end}]}]。

    有空格的語言會在字前面補空格，讓後續一律用 "".join 還原文字。
    """
    from whisperx.alignment import align as wx_align

    model, metadata = aligner
    result = wx_align(
        [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segments],
        model,
        metadata,
        audio,
        torch_device(),
        return_char_alignments=False,
    )
    spaced = language not in NO_SPACE_LANGUAGES
    out: list[dict[str, Any]] = []
    for seg in result.get("segments", []):
        raw_words = [
            {"text": str(w.get("word", "")), "start": _num(w.get("start")), "end": _num(w.get("end"))}
            for w in seg.get("words") or []
        ]
        words = restore_spaces(str(seg.get("text", "")), raw_words, spaced)
        out.append(
            {"start": _num(seg.get("start")), "end": _num(seg.get("end")), "text": seg.get("text", ""),
             "words": words}
        )
    return out


def _num(v: Any) -> float | None:
    """WhisperX 可能給 numpy 浮點數或 NaN，統一成 float／None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def restore_spaces(text: str, words: list[dict[str, Any]], spaced: bool) -> list[dict[str, Any]]:
    """把空格補回字的前面，之後一律用 "".join 還原文字。

    WhisperX 在中文模式以「字」為單位且會丟掉空白，英文夾雜時 "open source" 會變成
    "o p e n s o u r c e" 九個字；這裡對照原句，原本有空白的位置在下一個字前面補一個空格。
    """
    out: list[dict[str, Any]] = []
    pos = 0
    for i, w in enumerate(words):
        token = w["text"]
        j = text.find(token, pos) if token else -1
        if j >= 0:
            gap = text[pos:j]
            prefix = " " if (i > 0 and gap and gap.isspace()) else ""
            pos = j + len(token)
        else:
            prefix = " " if (spaced and i > 0) else ""
        out.append({**w, "text": prefix + token})
    return out


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def segment_language(text: str, detected: str) -> str:
    """依文字判斷對齊用的語言：有中文字用 zh，只有英文字母用 en，其餘沿用偵測結果。"""
    if _CJK_RE.search(text):
        return detected if detected in ("ja", "yue") else "zh"
    if _LATIN_RE.search(text):
        return "en"
    return detected or "zh"


def group_by_language(segments: list[dict[str, Any]], detected: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for seg in segments:
        groups.setdefault(segment_language(seg.get("text", ""), detected), []).append(seg)
    return groups


def unaligned(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """對齊失敗時的退路：整段當成一個字。"""
    return [{**s, "words": []} for s in segments]
