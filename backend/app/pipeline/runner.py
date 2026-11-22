"""推論流程編排：轉檔 → 轉錄 → 對齊 → 講者分段 → 逐字指派 → 聲紋比對 → 繁體＋字典。

YouTube 有人工字幕時（JobInput.captions）走字幕路線：跳過轉錄與逐字對齊（不載入 Whisper），
直接用字幕的文字與時間，講者分段之後依時間重疊把講者套到字幕段落上。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import dictionary as dict_mod
from ..config import ModelOption, settings
from ..i18n import Message, message_of, msg
from . import audio as audio_mod
from .align import group_by_language, unaligned
from .assign import assign_speakers
from .backend import Backend
from .captions import assign_caption_speakers
from .diarize import DiarizationUnavailable
from .errors import GpuBusy, is_oom
from .postprocess import AUTO, apply_to_segments, mode_for_captions

log = logging.getLogger(__name__)

# 各步驟在整體進度條中的區間；顯示文字是 i18n 的 stage.<key>
STAGES: dict[str, tuple[float, float]] = {
    "convert": (0.00, 0.05),
    "transcribe": (0.05, 0.60),
    "align": (0.60, 0.66),
    "diarize": (0.66, 0.92),
    "voiceprint": (0.92, 0.96),
    "postprocess": (0.96, 1.00),
}
# 字幕路線：沒有轉錄與對齊，講者辨識占大部分時間
CAPTION_STAGES: dict[str, tuple[float, float]] = {
    "convert": (0.00, 0.10),
    "captions": (0.10, 0.15),
    "diarize": (0.15, 0.85),
    "voiceprint": (0.85, 0.95),
    "postprocess": (0.95, 1.00),
}


@dataclass
class JobInput:
    job_id: str
    source: Path
    playback: Path
    model: ModelOption
    diarize: bool
    num_speakers: int | None
    dictionary: dict_mod.Dictionary
    voiceprints: list[dict[str, Any]] = field(default_factory=list)
    # YouTube 人工字幕（captions.parse 的輸出）；有值時不跑轉錄與對齊
    captions: list[dict[str, Any]] | None = None
    caption_lang: str | None = None
    # 在逐字稿裡記錄來源用（YouTube 標題、網址等），不影響處理
    source_info: dict[str, Any] | None = None


@dataclass
class JobOutput:
    transcript: dict[str, Any]
    duration: float | None
    playback_created: bool


Reporter = Callable[[str, Message, float], None]  # (stage key, 顯示文字（訊息 stage.<key>）, 整體進度 0~1)


def _reporter(report: Reporter, key: str,
              stages: dict[str, tuple[float, float]] = STAGES) -> Callable[[float], None]:
    lo, hi = stages[key]
    label = msg(f"stage.{key}")

    def on_progress(frac: float) -> None:
        report(key, label, lo + (hi - lo) * max(0.0, min(1.0, frac)))

    return on_progress


def relabel_by_appearance(segments: list[dict[str, Any]], *maps: dict[str, Any]) -> list[dict[str, Any]]:
    """講者依第一次出現的順序重新編號為 SPEAKER_00、SPEAKER_01…"""
    mapping: dict[str, str] = {}
    for seg in segments:
        spk = seg.get("speaker")
        if spk and spk not in mapping:
            mapping[spk] = f"SPEAKER_{len(mapping):02d}"
    for seg in segments:
        if seg.get("speaker"):
            seg["speaker"] = mapping[seg["speaker"]]
    out = []
    for m in maps:
        out.append({mapping[k]: v for k, v in m.items() if k in mapping})
    return out


def _clock(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def dropped_warning(dropped: list[dict[str, Any]], limit: int = 5) -> Message:
    """被當成提示詞幻覺略過的段落：附時間與內容寫進警告，讓使用者知道哪裡被拿掉了。"""
    items = []
    for d in dropped[:limit]:
        text = str(d.get("text") or "")
        if len(text) > 40:
            text = text[:40] + "…"
        items.append(msg("warning.dropped_item", time=_clock(d.get("start") or 0), text=text))
    more = msg("warning.dropped_more", count=len(dropped) - limit) if len(dropped) > limit else ""
    return msg("warning.dropped", count=len(dropped), items=items, more=more)


def run(inp: JobInput, backend: Backend, report: Reporter, check_cancel: Callable[[], None]) -> JobOutput:
    work_wav = settings.work_dir / f"{inp.job_id}.wav"
    warnings: list[Message] = []  # 可延後翻譯的訊息（見 app/i18n.py）
    oom = False
    use_captions = bool(inp.captions)
    stages = CAPTION_STAGES if use_captions else STAGES

    def stage(key: str) -> Callable[[float], None]:
        return _reporter(report, key, stages)

    try:
        # 1. 轉檔
        stage("convert")(0.0)
        probe = audio_mod.probe(inp.source)
        stream = audio_mod.convert_to_wav(inp.source, work_wav, probe.audio_streams)
        playback_created = False
        if not inp.playback.exists():
            try:
                audio_mod.make_playback(inp.source, inp.playback, stream)
                playback_created = True
            except audio_mod.AudioError as e:
                warnings.append(msg("warning.playback_failed", error=e.msg))
        audio = audio_mod.load_wav(work_wav)
        duration = len(audio) / audio_mod.SAMPLE_RATE
        stage("convert")(1.0)
        check_cancel()

        if use_captions:
            # 2'. 字幕路線：直接用 YouTube 人工字幕的文字與時間（不轉錄、不對齊）
            stage("captions")(0.0)
            aligned = [dict(seg) for seg in inp.captions or []]
            aligned.sort(key=lambda s: float(s.get("start") or 0.0))
            language = (inp.caption_lang or "").split("-")[0] or "zh"
            stage("captions")(1.0)
        else:
            # 2. 轉錄
            hints = dict_mod.hint_terms(inp.dictionary)
            on_tr = stage("transcribe")
            on_tr(0.0)
            dropped: list[dict[str, Any]] = []
            segments, language = backend.transcribe(audio, inp.model, hints, duration, on_tr, check_cancel,
                                                    on_dropped=dropped.append)
            if dropped:
                warnings.append(dropped_warning(dropped))
            check_cancel()

            # 3. 逐字對齊
            # 中英夾雜：依每段文字分組，各自用對應語言的對齊模型
            on_al = stage("align")
            on_al(0.0)
            aligned = []
            groups = group_by_language(segments, language)
            for i, (lang, group) in enumerate(groups.items()):
                try:
                    aligned.extend(backend.align(group, audio, lang))
                except Exception as e:  # noqa: BLE001
                    if is_oom(e):
                        raise
                    log.exception("逐字對齊失敗（%s）", lang)
                    warnings.append(msg("warning.align_failed", lang=lang, error=message_of(e)))
                    aligned.extend(unaligned(group))
                on_al((i + 1) / len(groups))
                check_cancel()
            aligned.sort(key=lambda s: float(s.get("start") or 0.0))

        # 4. 講者分段
        turns = None
        diar_info: dict[str, Any] = {"enabled": inp.diarize, "ok": False, "error": None,
                                     "num_speakers": inp.num_speakers}
        if inp.diarize and aligned:
            on_d = stage("diarize")
            on_d(0.0)
            try:
                turns = backend.diarize(audio, inp.num_speakers, on_d)
                diar_info["ok"] = True
            except Exception as e:  # noqa: BLE001
                if is_oom(e):
                    raise
                reason = e.msg if isinstance(e, DiarizationUnavailable) else msg("diarize.failed", error=message_of(e))
                log.warning("講者辨識失敗：%s", e, exc_info=not isinstance(e, DiarizationUnavailable))
                diar_info["error"] = reason
                warnings.append(reason)
            check_cancel()

        # 5. 逐字指派講者、換講者處切句（字幕沒有逐字時間時整段指派，不切句）
        if use_captions:
            out_segments = assign_caption_speakers(aligned, turns)
        else:
            out_segments = assign_speakers(aligned, turns)

        # 6. 聲紋比對
        embeddings: dict[str, list[float]] = {}
        speakers: dict[str, dict[str, Any]] = {}
        if turns:
            on_v = stage("voiceprint")
            on_v(0.0)
            try:
                embeddings = backend.speaker_embeddings(audio, turns)
            except Exception as e:  # noqa: BLE001
                if is_oom(e):
                    raise
                log.exception("計算講者聲紋失敗")
                warnings.append(msg("warning.voiceprint_failed", error=message_of(e)))
            on_v(1.0)
            (embeddings,) = relabel_by_appearance(out_segments, embeddings)
            from ..voiceprint import match

            matched = match(embeddings, inp.voiceprints, settings.voiceprint_threshold) if embeddings else {}
            for seg in out_segments:
                label = seg.get("speaker")
                if label and label not in speakers:
                    m = matched.get(label)
                    speakers[label] = {
                        "name": m["name"] if m else None,
                        "voiceprint_id": m["voiceprint_id"] if m else None,
                        "similarity": m["similarity"] if m else None,
                    }
        diar_info["detected_speakers"] = len(speakers)

        # 7. 繁體＋字典替換（繁體字幕不跑 OpenCC；模型輸出與簡體字幕逐段判斷，見 postprocess.py）
        stage("postprocess")(0.0)
        apply_to_segments(out_segments, inp.dictionary,
                          mode_for_captions(inp.caption_lang) if use_captions else AUTO)
        out_segments = [s for s in out_segments if s["text"].strip()]
        for i, seg in enumerate(out_segments):
            seg["id"] = i
            seg["start"] = round(float(seg["start"]), 3)
            seg["end"] = round(float(seg["end"]), 3)

        if use_captions:
            from ..youtube import caption_label

            text_source = {"type": "youtube_captions", "lang": inp.caption_lang,
                           "label": caption_label(inp.caption_lang)}
        else:
            text_source = {"type": "model", "model": inp.model.id, "label": inp.model.label_msg()}
        transcript = {
            "version": 1,
            "language": language,
            "duration": round(duration, 3),
            # 走字幕路線時沒有用辨識模型
            "model": None if use_captions else inp.model.id,
            "text_source": text_source,
            "diarization": diar_info,
            "speakers": speakers,
            "speaker_embeddings": embeddings,
            "embedding_model": backend.embedding_model if embeddings else None,
            "segments": out_segments,
            "warnings": warnings,
        }
        if inp.source_info:
            transcript["source"] = inp.source_info
        stage("postprocess")(1.0)
        return JobOutput(transcript=transcript, duration=duration, playback_created=playback_created)
    except Exception as e:
        if not is_oom(e):
            raise
        # 只記旗標：在 except 裡卸載的話，traceback 還抓著失敗步驟的 frame，模型釋放不掉
        log.warning("CUDA 記憶體不足：%s", e)
        oom = True
    finally:
        work_wav.unlink(missing_ok=True)
    if oom:
        try:
            backend.unload_all()
        except Exception:  # noqa: BLE001
            log.exception("卸載模型失敗")
    raise GpuBusy()
