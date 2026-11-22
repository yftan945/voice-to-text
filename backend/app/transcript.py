"""逐字稿資料結構與編輯操作（純 Python）。

原始結果（original）推論完就不再改；所有編輯都套在 edited 副本上，重設時把 edited 清掉即可。

結構：
{
  "version": 1, "language": "zh", "duration": 秒, "model": "large-v3",
  "diarization": {"enabled": bool, "ok": bool, "error": str|None, "num_speakers": int|None},
  "speakers": {"SPEAKER_00": {"name": str|None, "voiceprint_id": int|None, "similarity": float|None}},
  "speaker_embeddings": {"SPEAKER_00": [float...]},   # 只在 original，用來「存成聲紋」
  "embedding_model": str|None,
  "segments": [{"id": int, "start": 秒, "end": 秒, "speaker": str|None, "text": str}],
  "warnings": [str]
}
"""
from __future__ import annotations

import copy
import re
from typing import Any

from . import i18n
from .i18n import DEFAULT_LANG, LocalizedError


class EditError(LocalizedError, ValueError):
    """編輯參數不對；msg 可以直接顯示給使用者（見 i18n 的 transcript.*）。"""


_AUTO_LABEL_RE = re.compile(r"^SPEAKER_(\d+)$")


def default_speaker_name(label: str, lang: str = DEFAULT_LANG) -> str:
    """沒取名的講者顯示的名字：辨識出來的 SPEAKER_xx 顯示成「講者 N」／「Speaker N」（N＝代號數字＋1，
    和結果頁頭像上的數字一致）；其他代號（手動新增的 MANUAL_xx）照原樣。與前端 lib/speakers.ts 的 speakerName() 一致。"""
    m = _AUTO_LABEL_RE.match(label)
    return i18n.render("speaker.default", {"n": int(m.group(1)) + 1}, lang) if m else label


def display_name(transcript: dict[str, Any], label: str | None, lang: str = DEFAULT_LANG) -> str:
    if not label:
        return ""
    info = (transcript.get("speakers") or {}).get(label) or {}
    return info.get("name") or default_speaker_name(label, lang)


def localize_texts(t: dict[str, Any], lang: str) -> dict[str, Any]:
    """逐字稿裡給使用者看的文字（warnings、講者辨識失敗原因、文字來源說明）依語言 render。

    回傳淺層複本，不改原本的 dict。舊資料存的是中文字串，照原樣。逐字稿內容本身不翻譯。
    """
    out = dict(t)
    if "warnings" in out:
        out["warnings"] = i18n.localize_list(out.get("warnings"), lang)
    diar = out.get("diarization")
    if isinstance(diar, dict) and diar.get("error") is not None:
        out["diarization"] = {**diar, "error": i18n.localize(diar["error"], lang)}
    ts = out.get("text_source")
    if isinstance(ts, dict) and "label" in ts:
        out["text_source"] = {**ts, "label": i18n.localize(ts["label"], lang)}
    return out


def localize_summary(summary: dict[str, Any] | None, lang: str) -> dict[str, Any] | None:
    """講者摘要（jobs.speaker_summary）存的是 zh-TW 的顯示名稱：沒取名的「講者 N」換成該語言的預設名稱。"""
    if not summary or lang == DEFAULT_LANG or not isinstance(summary.get("speakers"), list):
        return summary
    speakers = []
    for s in summary["speakers"]:
        label = s.get("label") if isinstance(s, dict) else None
        if label and s.get("name") == default_speaker_name(label):
            s = {**s, "name": default_speaker_name(label, lang)}
        speakers.append(s)
    return {**summary, "speakers": speakers}


def make_edited(original: dict[str, Any]) -> dict[str, Any]:
    edited = copy.deepcopy(original)
    edited.pop("speaker_embeddings", None)
    return edited


def _segment(t: dict[str, Any], seg_id: int) -> dict[str, Any]:
    for seg in t.get("segments", []):
        if seg.get("id") == seg_id:
            return seg
    raise EditError("transcript.segment.not_found", id=seg_id)


# 手動新增的講者用不同的前綴，不會跟辨識出來的 SPEAKER_xx（以及它們的聲紋）撞名
MANUAL_PREFIX = "MANUAL_"


def reserved_labels(original: dict[str, Any] | None) -> set[str]:
    """原始結果用過的所有講者代號（含有聲紋的），新講者不能重複使用。"""
    if not original:
        return set()
    return set((original.get("speakers") or {}).keys()) | set((original.get("speaker_embeddings") or {}).keys())


def _next_label(t: dict[str, Any], reserved: set[str] | None = None) -> str:
    used = set((t.get("speakers") or {}).keys()) | set(reserved or ())
    n = 0
    while True:
        label = f"{MANUAL_PREFIX}{n:02d}"
        if label not in used:
            return label
        n += 1


def voiceprint_labels(original: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """可以「存成聲紋」的講者：原始結果裡有 embedding，且目前版本裡這個代號還是同一個人。

    「同一個人」的判斷：目前掛在這個代號下的段落，至少有一段在原始結果也是這個代號。
    舊版曾把手動新增的講者取名成被合併掉的 SPEAKER_xx，這種代號的段落全都不是原本那個人，要排除，
    否則會把別人的聲紋存成新講者的名字。
    """
    emb = original.get("speaker_embeddings") or {}
    speakers = current.get("speakers") or {}
    orig_by_id = {seg.get("id"): seg.get("speaker") for seg in original.get("segments", [])}
    same: set[str] = set()
    for seg in current.get("segments", []):
        label = seg.get("speaker")
        if label and orig_by_id.get(seg.get("id")) == label:
            same.add(label)
    return sorted(label for label in emb if label in speakers and label in same)


def rename_speaker(t: dict[str, Any], label: str, name: str) -> None:
    speakers = t.setdefault("speakers", {})
    if label not in speakers:
        raise EditError("transcript.speaker.not_found", label=label)
    name = (name or "").strip()
    speakers[label]["name"] = name or None


def set_segment_text(t: dict[str, Any], seg_id: int, text: str) -> None:
    seg = _segment(t, seg_id)
    seg["text"] = text.strip()


def set_segment_speaker(
    t: dict[str, Any], seg_id: int, label: str | None = None, new_name: str | None = None,
    reserved: set[str] | None = None,
) -> str:
    """改單段講者；label 為 None 且給了 new_name 時新增一位講者（代號避開 reserved）。回傳最後用的 label。"""
    seg = _segment(t, seg_id)
    speakers = t.setdefault("speakers", {})
    if label is None:
        if not (new_name or "").strip():
            raise EditError("transcript.speaker.required")
        label = _next_label(t, reserved)
        speakers[label] = {"name": new_name.strip(), "voiceprint_id": None, "similarity": None}
    elif label not in speakers:
        raise EditError("transcript.speaker.not_found", label=label)
    seg["speaker"] = label
    return label


def merge_speakers(t: dict[str, Any], source: str, target: str) -> None:
    """把 source 講者的所有段落改成 target，並移除 source。"""
    speakers = t.setdefault("speakers", {})
    if source == target:
        raise EditError("transcript.merge.same")
    if source not in speakers or target not in speakers:
        raise EditError("transcript.merge.not_found")
    for seg in t.get("segments", []):
        if seg.get("speaker") == source:
            seg["speaker"] = target
    del speakers[source]


def prune_unused_speakers(t: dict[str, Any]) -> None:
    used = {seg.get("speaker") for seg in t.get("segments", [])}
    for label in list((t.get("speakers") or {}).keys()):
        if label not in used:
            del t["speakers"][label]


def apply_edit(t: dict[str, Any], op: dict[str, Any], reserved: set[str] | None = None) -> dict[str, Any]:
    """套用一個編輯操作（API 用）。回傳修改後的同一個 dict。

    reserved：新增講者時不能用的代號（傳 `reserved_labels(original)`）。
    """
    kind = op.get("op")
    if kind == "rename_speaker":
        rename_speaker(t, str(op["speaker"]), str(op.get("name") or ""))
    elif kind == "set_text":
        set_segment_text(t, int(op["segment_id"]), str(op.get("text") or ""))
    elif kind == "set_speaker":
        set_segment_speaker(t, int(op["segment_id"]), op.get("speaker"), op.get("new_name"), reserved)
        prune_unused_speakers(t)
    elif kind == "merge_speakers":
        merge_speakers(t, str(op["source"]), str(op["target"]))
    else:
        raise EditError("transcript.edit.unsupported", op=kind)
    return t


# ---- 講者分布摘要（首頁的迷你色帶與占比用） ----

# 同一位講者的兩段間隔小於這個秒數就併成一段，色帶才不會碎成條碼
SUMMARY_MERGE_GAP = 1.5
# 色帶最多幾段；超過時把時間軸切成這麼多格，每格取講最多的人
SUMMARY_MAX_RUNS = 120


def speaker_summary(t: dict[str, Any], duration: float | None = None) -> dict[str, Any]:
    """每位講者的時間分布摘要（存在 jobs.speaker_summary，列表 API 直接帶出，不用載入整份逐字稿）。

    {
      "duration": 秒,
      "speakers": [{"label", "name", "seconds", "share"}],   # 依代號排序（MANUAL_ 在後），索引就是講者色的順序
      "runs": [[開始, 結束, 講者索引], ...]                  # 已合併、最多 SUMMARY_MAX_RUNS 段
    }
    """
    # 辨識出來的講者在前、手動新增的（MANUAL_xx）在後：新增講者時原本的講者顏色不會變
    labels = sorted((t.get("speakers") or {}).keys(), key=lambda label: (label.startswith(MANUAL_PREFIX), label))
    index = {label: i for i, label in enumerate(labels)}
    segs = sorted(
        ((float(s.get("start") or 0), float(s.get("end") or 0), index[s["speaker"]])
         for s in t.get("segments", []) if s.get("speaker") in index),
        key=lambda x: x[0],
    )
    seconds = [0.0] * len(labels)
    for start, end, i in segs:
        seconds[i] += max(0.0, end - start)
    total = sum(seconds)
    dur = float(duration or t.get("duration") or 0)
    dur = max(dur, max((float(s.get("end") or 0) for s in t.get("segments", [])), default=0.0))

    runs: list[list[float]] = []
    for start, end, i in segs:
        last = runs[-1] if runs else None
        if last is not None and last[2] == i and start - last[1] < SUMMARY_MERGE_GAP:
            last[1] = max(last[1], end)
        else:
            runs.append([start, end, i])
    if len(runs) > SUMMARY_MAX_RUNS and dur > 0:
        runs = _binned_runs(segs, dur, len(labels))

    return {
        "duration": round(dur, 2),
        "speakers": [
            {"label": label, "name": display_name(t, label), "seconds": round(seconds[i], 1),
             "share": round(seconds[i] / total, 4) if total else 0.0}
            for i, label in enumerate(labels)
        ],
        "runs": [[round(a, 1), round(b, 1), int(i)] for a, b, i in runs],
    }


def _binned_runs(segs: list[tuple[float, float, int]], dur: float, n_speakers: int) -> list[list[float]]:
    """把時間軸切成 SUMMARY_MAX_RUNS 格，每格取說話時間最多的講者（說話不到一成的格子當作靜默），再合併相鄰同講者的格子。"""
    bins = SUMMARY_MAX_RUNS
    width = dur / bins
    acc = [[0.0] * n_speakers for _ in range(bins)]
    for start, end, i in segs:
        b = max(0, min(bins - 1, int(start / width)))
        while b < bins and b * width < end:
            lo, hi = b * width, (b + 1) * width
            acc[b][i] += max(0.0, min(end, hi) - max(start, lo))
            b += 1
    runs: list[list[float]] = []
    for b, row in enumerate(acc):
        best = max(range(n_speakers), key=lambda k: row[k]) if n_speakers else -1
        if best < 0 or row[best] < width * 0.1:
            continue
        lo, hi = b * width, (b + 1) * width
        last = runs[-1] if runs else None
        if last is not None and last[2] == best and abs(last[1] - lo) < 1e-6:
            last[1] = hi
        else:
            runs.append([lo, hi, best])
    return runs
