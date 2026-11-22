"""推論流程的例外與錯誤分類。"""
from __future__ import annotations

from ..i18n import DEFAULT_LANG, LocalizedError, render

# zh-TW 文字（log 與舊程式用）；給使用者看的是 i18n 的 gpu.busy
GPU_BUSY_MESSAGE = render("gpu.busy", {}, DEFAULT_LANG)


class Canceled(Exception):
    """使用者取消任務。"""


class GpuBusy(LocalizedError, RuntimeError):
    def __init__(self) -> None:
        super().__init__("gpu.busy")


def is_oom(exc: BaseException) -> bool:
    """CUDA 記憶體不足：torch 的 OutOfMemoryError，或 CTranslate2／cuDNN 的 out of memory 訊息。"""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if type(e).__name__ == "OutOfMemoryError":
            return True
        msg = str(e).lower()
        if "out of memory" in msg or "cuda_error_out_of_memory" in msg or "cudnn_status_alloc_failed" in msg:
            return True
        e = e.__cause__ or e.__context__
    return False


def is_gated_error(exc: BaseException) -> bool:
    """HF gated 模型尚未同意條款或 token 無權限。"""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        name = type(e).__name__
        msg = str(e).lower()
        if name in ("GatedRepoError", "RepositoryNotFoundError") or "gated" in msg or "403" in msg or "401" in msg:
            return True
        e = e.__cause__ or e.__context__
    return False
