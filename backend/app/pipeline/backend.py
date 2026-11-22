"""推論後端介面。

runner 只透過 Backend 介面呼叫 GPU 相關步驟；測試時換成假的 Backend，就能在沒有 GPU、
沒有安裝 torch／faster-whisper／pyannote 的環境跑完整流程。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, Sequence

from ..config import ModelOption, settings
from .assign import Turn
from .errors import is_oom
from .models import ModelManager, free_gpu_memory

log = logging.getLogger(__name__)

# 講者語音太短（秒）就不算聲紋，避免誤配
MIN_EMBED_SECONDS = 2.0


class Backend(Protocol):
    def transcribe(
        self, audio: Any, model: ModelOption, hint_terms: list[str], duration: float,
        on_progress: Callable[[float], None], check_cancel: Callable[[], None],
        on_dropped: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[list[dict[str, Any]], str]: ...

    def align(self, segments: list[dict[str, Any]], audio: Any, language: str) -> list[dict[str, Any]]: ...

    def diarize(self, audio: Any, num_speakers: int | None,
                on_progress: Callable[[float], None]) -> list[Turn]: ...

    def speaker_embeddings(self, audio: Any, turns: Sequence[Turn]) -> dict[str, list[float]]: ...

    def embed(self, audio: Any) -> list[float]: ...

    @property
    def embedding_model(self) -> str: ...

    def unload_all(self) -> None: ...


def retry_after_oom(manager: ModelManager, fn: Callable[[], Any], keep: str | None = None) -> Any:
    """CUDA OOM 時先卸載自己快取中、目前沒在用的其他模型，再重試一次；仍失敗就往上丟（任務失敗）。

    except 區塊裡只記旗標：traceback 還抓著失敗那次呼叫的 frame（模型與中間張量的參考），
    在 except 裡重試會讓 VRAM 同時留著兩份。離開 except 後才卸載、gc、清 torch 快取、重試。
    `keep` 是這一步要用的模型，保留不卸載。
    """
    oom = False
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        if not is_oom(e):
            raise
        log.warning("CUDA 記憶體不足，卸載閒置模型後重試一次：%s", e)
        oom = True
    if oom:
        manager.unload_all(keep=keep)
        free_gpu_memory()
    return fn()


class GpuBackend:
    """實際推論：faster-whisper → WhisperX → pyannote，模型交給 ModelManager 管理生命週期。"""

    def __init__(self, manager: ModelManager) -> None:
        self.manager = manager

    def _use(self, key: str, loader: Callable[[], Any], fn: Callable[[Any], Any], retry: bool = True) -> Any:
        def attempt() -> Any:
            with self.manager.use(key, loader) as model:
                return fn(model)

        if not retry:
            return attempt()
        return retry_after_oom(self.manager, attempt, keep=key)

    @property
    def embedding_model(self) -> str:
        return settings.embedding_model

    def transcribe(self, audio, model, hint_terms, duration, on_progress, check_cancel, on_dropped=None):
        from . import asr

        return self._use(
            f"whisper:{model.id}", lambda: asr.load_whisper(model),
            lambda m: asr.transcribe(m, audio, hint_terms=hint_terms, duration=duration,
                                     on_progress=on_progress, check_cancel=check_cancel,
                                     on_dropped=on_dropped),
        )

    def align(self, segments, audio, language):
        from . import align

        return self._use(f"align:{language}", lambda: align.load_aligner(language),
                         lambda a: align.align(a, segments, audio, language))

    def diarize(self, audio, num_speakers, on_progress):
        from . import diarize

        return self._use("diarization", diarize.load_pipeline,
                         lambda p: diarize.run_diarization(p, audio, num_speakers=num_speakers,
                                                           on_progress=on_progress))

    def speaker_embeddings(self, audio, turns):
        from . import diarize

        labels = sorted({t[2] for t in turns})

        def run(inf: Any) -> dict[str, list[float]]:
            out: dict[str, list[float]] = {}
            for label in labels:
                clip = diarize.speaker_audio(audio, turns, label)
                if clip is None or len(clip) < MIN_EMBED_SECONDS * diarize.SAMPLE_RATE:
                    continue
                out[label] = diarize.embed_audio(inf, clip)
            return out

        return self._use("embedding", diarize.load_embedding_model, run)

    def embed(self, audio):
        """聲紋登記（API 請求）用：OOM 時不重試、不卸載任何模型（那些可能是 worker 正要用的），
        直接往上丟，由 API 回「GPU 忙碌」。"""
        from . import diarize

        return self._use("embedding", diarize.load_embedding_model,
                         lambda inf: diarize.embed_audio(inf, audio), retry=False)

    def unload_all(self) -> None:
        self.manager.unload_all()
