"""匯出：TXT、Markdown、SRT、VTT、JSON（純 Python）。匯出一律用編輯後的版本。

lang 影響沒取名講者的名稱（講者 N／Speaker N）、講者與文字之間的格式與預設標題；逐字稿內容本身不翻譯。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .i18n import DEFAULT_LANG, render as render_msg
from .transcript import display_name, localize_texts

FORMATS = {
    "txt": ("text/plain; charset=utf-8", "txt"),
    "md": ("text/markdown; charset=utf-8", "md"),
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "vtt": ("text/vtt; charset=utf-8", "vtt"),
    "json": ("application/json; charset=utf-8", "json"),
}


def _ms(seconds: float) -> int:
    return max(0, int(round(float(seconds) * 1000)))


def fmt_clock(seconds: float) -> str:
    """[hh:mm:ss] 用的時間（無毫秒）。"""
    total = _ms(seconds) // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_srt_time(seconds: float) -> str:
    ms = _ms(seconds)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt_time(seconds: float) -> str:
    return fmt_srt_time(seconds).replace(",", ".")


# 同一位講者的兩段之間停頓超過這個秒數就換行（純文字模式與 TXT）
PARAGRAPH_GAP = 2.0
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def paragraphs(t: dict[str, Any]) -> list[dict[str, Any]]:
    """把連續同講者的段落合併成一個段落（純文字模式用）。`segs` 保留各段的時間，用來判斷停頓。"""
    out: list[dict[str, Any]] = []
    for seg in t.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        item = {"start": float(seg["start"]), "end": float(seg["end"]), "text": text}
        if out and out[-1]["speaker"] == seg.get("speaker"):
            out[-1]["texts"].append(text)
            out[-1]["segs"].append(item)
            out[-1]["end"] = seg["end"]
        else:
            out.append(
                {"speaker": seg.get("speaker"), "start": seg["start"], "end": seg["end"], "texts": [text],
                 "segs": [item]}
            )
    return out


def _separator(prev: str, nxt: str) -> str:
    """兩段文字接起來時中間要補什麼：
    - 英文與英文之間補空格（含英文句點後接下一句）
    - 中文接英文或數字、英文或數字接中文時補空格
    - 中文接中文、前一段沒有句末標點時補「，」，避免兩句黏在一起
    與前端 format.ts 的 joinSeparator() 一致。
    """
    if not prev or not nxt:
        return ""
    last, first = prev[-1], nxt[0]
    if last.isascii() and not last.isspace() and first.isascii() and first.isalnum():
        return " "
    if (_CJK_RE.match(last) and _ALNUM_RE.match(first)) or (_ALNUM_RE.match(last) and _CJK_RE.match(first)):
        return " "
    if _CJK_RE.match(last) and _CJK_RE.match(first):
        return "，"
    return ""


def join_segments(segs: list[dict[str, Any]], gap: float = PARAGRAPH_GAP) -> str:
    """合併同一位講者的連續段落；停頓超過 gap 秒時換行。"""
    out = ""
    prev_end: float | None = None
    for s in segs:
        if out and prev_end is not None and s["start"] - prev_end > gap:
            out += "\n" + s["text"]
        else:
            out += _separator(out, s["text"]) + s["text"]
        prev_end = s["end"]
    return out


def _line(name: str, text: str, lang: str) -> str:
    """有講者時「講者：文字」（英文用半形冒號加空白）；沒有講者時只有文字。"""
    return render_msg("export.speaker_line", {"name": name, "text": text}, lang) if name else text


def to_txt(t: dict[str, Any], timestamps: bool = True, lang: str = DEFAULT_LANG) -> str:
    lines: list[str] = []
    if timestamps:
        for seg in t.get("segments", []):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            name = display_name(t, seg.get("speaker"), lang)
            prefix = f"[{fmt_clock(seg['start'])}] "
            lines.append(prefix + _line(name, text, lang))
        return "\n".join(lines) + "\n"
    for p in paragraphs(t):
        name = display_name(t, p["speaker"], lang)
        text = join_segments(p["segs"])
        lines.append(_line(name, text, lang))
    return "\n\n".join(lines) + "\n"


def to_markdown(t: dict[str, Any], title: str | None = None, lang: str = DEFAULT_LANG) -> str:
    if title is None:
        title = render_msg("export.default_title", {}, lang)
    parts = [f"# {title}", ""]
    for p in paragraphs(t):
        name = display_name(t, p["speaker"], lang)
        head = f"**{name}** " if name else ""
        parts.append(f"{head}`[{fmt_clock(p['start'])}]`")
        parts.append("")
        # Markdown 的單一換行不會斷行，停頓處用行尾兩個空白強制換行
        parts.append(join_segments(p["segs"]).replace("\n", "  \n"))
        parts.append("")
    return "\n".join(parts)


def _cue_text(s: str) -> str:
    """字幕內文：空行會結束一個 cue、`-->` 會被當成時間行，都要清掉。"""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in s.split("\n")]
    s = "\n".join(line for line in lines if line)
    return s.replace("-->", "→")


def to_srt(t: dict[str, Any], lang: str = DEFAULT_LANG) -> str:
    blocks: list[str] = []
    n = 0
    for seg in t.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        text = _cue_text(text)
        n += 1
        name = _cue_text(display_name(t, seg.get("speaker"), lang))
        end = max(float(seg["end"]), float(seg["start"]) + 0.001)
        blocks.append(
            f"{n}\n{fmt_srt_time(seg['start'])} --> {fmt_srt_time(end)}\n" + _line(name, text, lang)
        )
    return "\n\n".join(blocks) + "\n"


def _vtt_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_vtt(t: dict[str, Any], lang: str = DEFAULT_LANG) -> str:
    blocks = ["WEBVTT"]
    for seg in t.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        text = _cue_text(text)
        name = _cue_text(display_name(t, seg.get("speaker"), lang))
        end = max(float(seg["end"]), float(seg["start"]) + 0.001)
        body = _vtt_escape(text)
        if name:
            body = f"<v {_vtt_escape(name)}>{body}"
        blocks.append(f"{fmt_vtt_time(seg['start'])} --> {fmt_vtt_time(end)}\n{body}")
    return "\n\n".join(blocks) + "\n"


def to_json(t: dict[str, Any], lang: str = DEFAULT_LANG) -> str:
    t = localize_texts(t, lang)
    data = {
        "language": t.get("language"),
        "duration": t.get("duration"),
        "model": t.get("model"),
        "text_source": t.get("text_source"),
        "source": t.get("source"),
        "speakers": {
            label: {"name": display_name(t, label, lang)} for label in (t.get("speakers") or {})
        },
        "segments": [
            {
                "id": seg.get("id"),
                "start": round(float(seg["start"]), 3),
                "end": round(float(seg["end"]), 3),
                "speaker": seg.get("speaker"),
                "speaker_name": display_name(t, seg.get("speaker"), lang) or None,
                "text": seg.get("text", ""),
            }
            for seg in t.get("segments", [])
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def render(t: dict[str, Any], fmt: str, *, title: str | None = None, timestamps: bool = True,
           lang: str = DEFAULT_LANG) -> str:
    """title 沒給時用該語言的預設標題（逐字稿／Transcript）。"""
    if fmt == "txt":
        return to_txt(t, timestamps=timestamps, lang=lang)
    if fmt == "md":
        return to_markdown(t, title=title, lang=lang)
    if fmt == "srt":
        return to_srt(t, lang=lang)
    if fmt == "vtt":
        return to_vtt(t, lang=lang)
    if fmt == "json":
        return to_json(t, lang=lang)
    raise ValueError(f"不支援的格式：{fmt}")
