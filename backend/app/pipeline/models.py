"""模型快取：用到才載入，閒置超過設定秒數（預設 10 分鐘）自動卸載並釋放 GPU 記憶體。"""
from __future__ import annotations

import gc
import logging
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)


def free_gpu_memory() -> None:
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 - 釋放記憶體失敗不影響流程
        log.exception("torch.cuda.empty_cache 失敗")


# 每個執行緒各自累計「花在載入模型的秒數」：worker 用來把載入時間從處理時間扣掉，估算等待時間比較準
_load_timer = threading.local()


def reset_load_timer() -> None:
    _load_timer.seconds = 0.0


def load_timer() -> float:
    """這個執行緒從上次 reset_load_timer() 之後花在載入模型的秒數。"""
    return float(getattr(_load_timer, "seconds", 0.0))


class _Entry:
    __slots__ = ("obj", "last_used", "in_use")

    def __init__(self, obj: Any, now: float) -> None:
        self.obj = obj
        self.last_used = now
        self.in_use = 0


class ModelManager:
    """模型快取。

    - `_lock` 只保護 `_entries` 這個 dict，持有時間很短；載入模型（可能要幾十秒）時改用每個 key
      自己的 lock，避免聲紋登記（API）載入 embedding 模型時卡住 worker 使用其他模型。
    - `clock` 可注入（測試用假時鐘），預設 `time.monotonic`。
    """

    def __init__(self, idle_seconds: float, check_interval: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        self._check_interval = check_interval

    def start(self) -> None:
        if self._reaper is None:
            self._reaper = threading.Thread(target=self._reap_loop, name="model-reaper", daemon=True)
            self._reaper.start()

    def stop(self) -> None:
        self._stop.set()

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = self._key_locks[key] = threading.Lock()
            return lock

    @contextmanager
    def use(self, key: str, loader: Callable[[], Any]) -> Iterator[Any]:
        # 同一個 key 同時只有一個執行緒在載入；不同 key 互不影響
        with self._key_lock(key):
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None:
                    entry.in_use += 1
            if entry is None:
                log.info("載入模型 %s", key)
                t0 = time.monotonic()
                obj = loader()  # 不持有全域 lock
                entry = _Entry(obj, self._clock())
                entry.in_use = 1
                with self._lock:
                    self._entries[key] = entry
                elapsed = time.monotonic() - t0
                _load_timer.seconds = load_timer() + elapsed
                log.info("模型 %s 載入完成（%.1f 秒）", key, elapsed)
        try:
            yield entry.obj
        finally:
            with self._lock:
                entry.in_use -= 1
                entry.last_used = self._clock()

    def loaded(self) -> list[str]:
        with self._lock:
            return sorted(self._entries.keys())

    def busy(self) -> bool:
        """是否有模型正在使用中。"""
        with self._lock:
            return any(e.in_use for e in self._entries.values())

    def _drop(self, key: str) -> bool:
        """從快取移除（使用中的不動）；回傳是否有移除。不釋放 GPU 記憶體，由呼叫端統一處理。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.in_use:
                return False
            del self._entries[key]
        log.info("卸載模型 %s", key)
        return True

    def unload(self, key: str) -> None:
        if self._drop(key):
            free_gpu_memory()

    def unload_all(self, keep: str | None = None) -> list[str]:
        """卸載所有沒在使用中的模型；`keep` 指定的那一顆保留（OOM 重試時保留目前這一步要用的模型）。"""
        dropped = [k for k in self.loaded() if k != keep and self._drop(k)]
        if dropped:
            free_gpu_memory()
        return dropped

    def unload_idle(self, now: float | None = None) -> list[str]:
        now = self._clock() if now is None else now
        with self._lock:
            idle = [
                k
                for k, e in self._entries.items()
                if e.in_use == 0 and now - e.last_used >= self.idle_seconds
            ]
        dropped = [k for k in idle if self._drop(k)]
        if dropped:
            free_gpu_memory()
        return dropped

    def _reap_loop(self) -> None:
        while not self._stop.wait(self._check_interval):
            try:
                self.unload_idle()
                # 沒有模型使用中時也釋放 torch 的快取區塊（例如任務 OOM 失敗後殘留的暫存配置）
                if not self.busy():
                    free_gpu_memory()
            except Exception:  # noqa: BLE001
                log.exception("卸載閒置模型失敗")
