"""pyannote.audio 4.x 講者分段與講者聲紋 embedding。"""
from __future__ import annotations

import logging
from typing import Any, Callable, Sequence

from ..config import settings
from ..i18n import DEFAULT_LANG, LocalizedError, render
from .assign import Turn

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# 計算講者 embedding 時最多取多少秒的語音（取該講者最長的幾段）
EMBED_MAX_SECONDS = 30.0
EMBED_MIN_TURN = 0.5

# zh-TW 文字（log 用）；給使用者看的是 i18n 的 diarize.gated
GATED_HINT = render("diarize.gated", {}, DEFAULT_LANG)


class DiarizationUnavailable(LocalizedError, RuntimeError):
    """講者辨識模型無法使用；msg 可以直接顯示給使用者（見 i18n 的 diarize.*）。"""


def _torch_device():
    import torch

    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_pipeline():
    if not settings.hf_token:
        raise DiarizationUnavailable("diarize.no_token")
    from pyannote.audio import Pipeline

    try:
        pipeline = Pipeline.from_pretrained(settings.diarization_model, token=settings.hf_token)
    except Exception as e:  # noqa: BLE001
        from .errors import is_gated_error

        if is_gated_error(e):
            raise DiarizationUnavailable("diarize.gated") from e
        raise
    if pipeline is None:  # 舊版 pyannote 在 gated 時會回傳 None 而不是丟例外
        raise DiarizationUnavailable("diarize.gated")
    pipeline.to(_torch_device())
    return pipeline


def _waveform(audio):
    import torch

    return {"waveform": torch.from_numpy(audio).float().unsqueeze(0), "sample_rate": SAMPLE_RATE}


def run_diarization(
    pipeline,
    audio,
    *,
    num_speakers: int | None,
    on_progress: Callable[[float], None],
) -> list[Turn]:
    # pyannote hook：hook(step_name, step_artifact, file=None, total=None, completed=None)
    steps = {"segmentation": (0.0, 0.25), "speaker_counting": (0.25, 0.3), "embeddings": (0.3, 0.9),
             "discrete_diarization": (0.9, 1.0)}

    def hook(step_name: str, step_artifact: Any, file: Any = None, total: int | None = None,
             completed: int | None = None) -> None:
        lo, hi = steps.get(step_name, (None, None))
        if lo is None:
            return
        frac = (completed / total) if (total and completed is not None) else 1.0
        on_progress(lo + (hi - lo) * min(1.0, frac))

    kwargs: dict[str, Any] = {"hook": hook}
    if num_speakers:
        kwargs["num_speakers"] = int(num_speakers)
    output = pipeline(_waveform(audio), **kwargs)
    # pyannote 4.x 回傳 DiarizeOutput；exclusive 版本沒有重疊，最適合逐字指派
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", output)
    turns: list[Turn] = [
        (float(seg.start), float(seg.end), str(label))
        for seg, _, label in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda t: t[0])
    return turns


def load_embedding_model(device: str | None = None):
    from pyannote.audio import Inference, Model

    token = settings.hf_token or None
    model = Model.from_pretrained(settings.embedding_model, token=token)
    if model is None:
        raise DiarizationUnavailable("diarize.embedding_unavailable", model=settings.embedding_model)
    import torch

    dev = torch.device(device) if device else _torch_device()
    return Inference(model, window="whole", device=dev)


def embed_audio(inference, audio) -> list[float]:
    import numpy as np

    vec = inference(_waveform(audio))
    vec = np.asarray(vec, dtype=np.float32).reshape(-1)
    return [float(x) for x in vec]


def speaker_audio(audio, turns: Sequence[Turn], speaker: str):
    """把某講者最長的幾段語音接起來（最多 EMBED_MAX_SECONDS 秒），用來算聲紋。"""
    import numpy as np

    own = sorted(
        [t for t in turns if t[2] == speaker and t[1] - t[0] >= EMBED_MIN_TURN],
        key=lambda t: t[1] - t[0],
        reverse=True,
    )
    pieces = []
    total = 0.0
    for t0, t1, _ in own:
        take = min(t1 - t0, EMBED_MAX_SECONDS - total)
        if take <= 0:
            break
        s = int(t0 * SAMPLE_RATE)
        e = int((t0 + take) * SAMPLE_RATE)
        pieces.append(audio[s:e])
        total += take
    if not pieces:
        return None
    return np.concatenate(pieces)
