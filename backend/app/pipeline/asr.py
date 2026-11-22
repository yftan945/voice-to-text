"""faster-whisper 轉錄。"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from ..config import ModelOption, settings

log = logging.getLogger(__name__)


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(s: str) -> str:
    return _PUNCT_RE.sub("", s).casefold()


def _covered_by(t: str, p: str) -> bool:
    """t 是否完全由 p 的片段（每段至少 2 字）接成，例如提示句後半重複兩次。"""
    i = 0
    while i < len(t):
        n = len(t) - i
        while n >= 2 and t[i : i + n] not in p:
            n -= 1
        if n < 2:
            return False
        i += n
    return True


def is_prompt_leak(text: str, prompt: str, terms: list[str], hotwords: str | None = None) -> bool:
    """Whisper 的已知幻覺：在靜音或結尾把 prompt／hotwords 原樣吐出來。

    判斷為外洩：
    - 整段（去標點後）是提示句的一部分（重複也算）
    - 整段只由兩個以上的提示詞組成
    - 整段是實際送出的 hotwords 字串（提示句＋提示詞）的片段，且同時含提示詞與一段提示句
      （有設 ASR_INITIAL_PROMPT 又有字典詞時，Whisper 會把整串「提示句 詞1、詞2」吐出來）
    只有一個提示詞的短段落（例如真的只講了「Kubernetes」）不算。
    """
    t = _norm(text)
    if not t:
        return False
    p = _norm(prompt)
    if p and len(t) >= 4 and _covered_by(t, p):
        return True
    normed = sorted({_norm(x) for x in terms if _norm(x)}, key=len, reverse=True)
    rest = t
    used = 0
    for term in normed:
        if term in rest:
            used += 1
            rest = rest.replace(term, "")
    if used >= 2 and not rest:
        return True
    h = _norm(hotwords or "")
    if h and p and used >= 1 and len(rest) >= 4 and _covered_by(t, h) and _covered_by(rest, p):
        return True
    return False


def build_hotwords(hint_terms: list[str]) -> str | None:
    """實際送給 faster-whisper 的 hotwords：提示句＋字典詞。外洩過濾也用同一個字串比對。"""
    prompt_parts = [settings.initial_prompt.strip()] if settings.initial_prompt.strip() else []
    if hint_terms:
        prompt_parts.append("、".join(hint_terms))
    return " ".join(prompt_parts) or None


def load_whisper(option: ModelOption):
    from faster_whisper import WhisperModel

    if cuda_available():
        device, compute_type = "cuda", settings.compute_type
    else:
        device, compute_type = "cpu", "int8"
    log.info("載入 faster-whisper %s（%s／%s）", option.path, device, compute_type)
    return WhisperModel(option.path, device=device, compute_type=compute_type)


def transcribe(
    model,
    audio,
    *,
    hint_terms: list[str],
    duration: float,
    on_progress: Callable[[float], None],
    check_cancel: Callable[[], None],
    on_dropped: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """回傳（段落清單, 語言）。段落：{start, end, text}。

    提示詞放在 hotwords：faster-whisper 會把它放進每個 30 秒視窗的 prompt，
    initial_prompt 在 condition_on_previous_text=False 時只影響第一個視窗。
    被當成提示詞外洩略過的段落會交給 on_dropped（runner 寫進警告），不會靜默丟掉。
    """
    hotwords = build_hotwords(hint_terms)
    segments_iter, info = model.transcribe(
        audio,
        language=settings.language or None,
        multilingual=settings.multilingual and not settings.language,
        beam_size=settings.beam_size,
        hotwords=hotwords,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    total = duration or float(getattr(info, "duration", 0) or 0) or 1.0
    out: list[dict[str, Any]] = []
    for seg in segments_iter:
        check_cancel()
        text = (seg.text or "").strip()
        if text and is_prompt_leak(text, settings.initial_prompt, hint_terms, hotwords):
            log.info("略過提示詞外洩的段落：%s", text)
            if on_dropped is not None:
                on_dropped({"start": float(seg.start), "end": float(seg.end), "text": text})
        elif text:
            out.append({"start": float(seg.start), "end": float(seg.end), "text": text})
        on_progress(min(1.0, float(seg.end) / total))
    return out, getattr(info, "language", None) or settings.language or "zh"
