"""測試用：假的推論後端與假的 ffmpeg，讓完整流程能在沒有 GPU 的環境跑。"""
from __future__ import annotations

import numpy as np

from app import i18n
from app.pipeline import audio as audio_mod
from app.pipeline.diarize import DiarizationUnavailable


class FakeBackend:
    embedding_model = "fake-embedding"

    # 字幕（沒有逐字時間）流程的測試用：照字幕時間 0~1.9、2.5~4.0 各給一個講者
    CAPTION_TURNS = [(0.0, 0.8, "SPEAKER_05"), (0.8, 2.0, "SPEAKER_02"), (2.4, 4.0, "SPEAKER_05")]

    def __init__(self, *, diarize_error=None, transcribe_error=None, align_error=None, turns=None):
        self.turns = turns
        self.diarize_error = diarize_error
        self.transcribe_error = transcribe_error
        self.align_error = align_error
        self.calls = []
        self.unloaded = False

    def transcribe(self, audio, model, hint_terms, duration, on_progress, check_cancel, on_dropped=None):
        self.calls.append(("transcribe", model.id, list(hint_terms)))
        if self.transcribe_error:
            raise self.transcribe_error
        on_progress(0.5)
        check_cancel()
        on_progress(1.0)
        return [
            {"start": 0.0, "end": 3.0, "text": "大家好我是亚当"},
            {"start": 3.5, "end": 4.0, "text": "人工只能很好"},
        ], "zh"

    def align(self, segments, audio, language):
        self.calls.append(("align",))
        if self.align_error:
            raise self.align_error
        out = []
        for seg in segments:
            chars = list(seg["text"])
            step = (seg["end"] - seg["start"]) / len(chars)
            out.append({**seg, "words": [
                {"text": c, "start": seg["start"] + i * step, "end": seg["start"] + (i + 1) * step}
                for i, c in enumerate(chars)]})
        return out

    def diarize(self, audio, num_speakers, on_progress):
        self.calls.append(("diarize", num_speakers))
        if self.diarize_error:
            raise self.diarize_error
        on_progress(1.0)
        # pyannote 的 label 順序不一定照出現順序
        if self.turns is not None:
            return list(self.turns)
        # 第二個講者要講超過 1.5 秒，才不會被當成短片段吸收
        return [(0.0, 1.3, "SPEAKER_05"), (1.3, 3.0, "SPEAKER_02"), (3.4, 4.0, "SPEAKER_05")]

    def speaker_embeddings(self, audio, turns):
        return {"SPEAKER_05": [1.0, 0.0, 0.0], "SPEAKER_02": [0.0, 1.0, 0.0]}

    def embed(self, audio):
        return [0.0, 0.0, 1.0]

    def unload_all(self):
        self.unloaded = True


def patch_audio(monkeypatch, seconds=4.0):
    """把 ffmpeg 相關函式換成假的。"""

    def probe(path):
        return audio_mod.ProbeResult(duration=seconds, audio_streams=2)

    def convert_to_wav(src, dst, num_streams=None):
        dst.write_bytes(b"RIFF")
        return 0

    def make_playback(src, dst, idx):
        dst.write_bytes(b"m4a")

    def load_wav(path):
        return np.zeros(int(seconds * audio_mod.SAMPLE_RATE), dtype=np.float32)

    monkeypatch.setattr(audio_mod, "probe", probe)
    monkeypatch.setattr(audio_mod, "convert_to_wav", convert_to_wav)
    monkeypatch.setattr(audio_mod, "make_playback", make_playback)
    monkeypatch.setattr(audio_mod, "load_wav", load_wav)


def zh(value):
    """資料庫或例外裡的訊息（code＋params）render 成 zh-TW 文字；一般字串原樣（舊資料）。清單逐項處理。"""
    if isinstance(value, list):
        return [i18n.localize(v, "zh-TW") for v in value]
    return i18n.localize(value, "zh-TW")


__all__ = ["FakeBackend", "patch_audio", "DiarizationUnavailable", "zh"]
