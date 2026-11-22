"""模型閒置卸載：用可控制的假時鐘，不真的等 10 分鐘。"""
from __future__ import annotations

import sys
import time
import types

import pytest

from app.config import Settings
from app.pipeline.models import ModelManager

TEN_MINUTES = 600


class FakeClock:
    """可以手動推進的時鐘，透過 ModelManager 的 clock 參數注入（不替換模組的 time）。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def __getitem__(self, i: int) -> float:  # 讓測試沿用 clock[0] 的寫法
        return self.now

    def __setitem__(self, i: int, value: float) -> None:
        self.now = value


@pytest.fixture()
def clock():
    return FakeClock()


def manager(clock, **kw):
    return ModelManager(idle_seconds=TEN_MINUTES, clock=clock, **kw)


def test_default_idle_is_ten_minutes(monkeypatch):
    """沒有設定環境變數時，閒置卸載時間是 10 分鐘。"""
    monkeypatch.delenv("IDLE_UNLOAD_SECONDS", raising=False)
    assert Settings().idle_unload_seconds == TEN_MINUTES


def test_not_unloaded_before_ten_minutes(clock):
    """閒置未滿 10 分鐘不卸載，滿 10 分鐘才卸載。"""
    mgr = manager(clock)
    with mgr.use("whisper", lambda: "W"):
        pass
    clock[0] += TEN_MINUTES - 1
    assert mgr.unload_idle() == []
    assert mgr.loaded() == ["whisper"]
    clock[0] += 1
    assert mgr.unload_idle() == ["whisper"]
    assert mgr.loaded() == []


def test_using_again_resets_idle_timer(clock):
    """再次使用會重新計時：從最後一次用完開始算閒置時間。"""
    mgr = manager(clock)
    with mgr.use("whisper", lambda: "W"):
        pass
    clock[0] += 500
    with mgr.use("whisper", lambda: pytest.fail("已載入的模型不應重新載入")):
        pass
    clock[0] += 500  # 距離載入 1000 秒，但距離上次使用只有 500 秒
    assert mgr.unload_idle() == []
    clock[0] += 100
    assert mgr.unload_idle() == ["whisper"]


def test_long_running_use_is_not_unloaded_and_counts_from_release(clock):
    """執行超過 10 分鐘的任務期間不卸載；閒置時間從用完那一刻開始算，而不是從載入時。"""
    mgr = manager(clock)
    with mgr.use("whisper", lambda: "W"):
        clock[0] += 3 * TEN_MINUTES
        assert mgr.unload_idle() == []
    clock[0] += 10
    assert mgr.unload_idle() == []
    assert mgr.loaded() == ["whisper"]


def test_only_idle_models_unloaded(clock):
    """只卸載閒置夠久的模型，其他模型保留。"""
    mgr = manager(clock)
    with mgr.use("old", lambda: "O"):
        pass
    clock[0] += 400
    with mgr.use("recent", lambda: "R"):
        pass
    clock[0] += 300
    assert mgr.unload_idle() == ["old"]
    assert mgr.loaded() == ["recent"]


def test_unloaded_model_is_reloaded_on_next_use(clock):
    """卸載後再用會重新載入。"""
    mgr = manager(clock)
    loads = []

    def loader():
        loads.append(1)
        return "W"

    with mgr.use("whisper", loader):
        pass
    clock[0] += TEN_MINUTES
    mgr.unload_idle()
    with mgr.use("whisper", loader) as m:
        assert m == "W"
    assert len(loads) == 2


def test_unload_releases_cuda_cache(clock, monkeypatch):
    """卸載後呼叫 torch.cuda.empty_cache 釋放 GPU 記憶體（用假的 torch 模組）。"""
    emptied = []
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(
        is_available=lambda: True, empty_cache=lambda: emptied.append(1)))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    mgr = manager(clock)
    with mgr.use("whisper", lambda: "W"):
        pass
    clock[0] += TEN_MINUTES
    mgr.unload_idle()
    assert emptied == [1]


def test_reaper_thread_unloads_idle_models(clock):
    """背景執行緒會定期檢查並卸載閒置模型（假時鐘推進，檢查間隔縮短）。"""
    mgr = manager(clock, check_interval=0.01)
    with mgr.use("whisper", lambda: "W"):
        pass
    mgr.start()
    try:
        # 還沒閒置夠久：reaper 跑過幾輪也不卸載
        time.sleep(0.1)
        assert mgr.loaded() == ["whisper"]
        clock[0] += TEN_MINUTES
        deadline = time.monotonic() + 5
        while mgr.loaded() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert mgr.loaded() == []
    finally:
        mgr.stop()
        mgr._reaper.join(5)
    assert not mgr._reaper.is_alive()


def test_app_uses_configured_idle_seconds(data_dir, monkeypatch):
    """沒有注入 backend 時，app 建立 ModelManager 並使用設定的閒置秒數。"""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "idle_unload_seconds", TEN_MINUTES)
    app = create_app(start_worker=False)
    with TestClient(app):
        mgr = app.state.model_manager
        assert isinstance(mgr, ModelManager)
        assert mgr.idle_seconds == TEN_MINUTES
        assert mgr._reaper is not None and mgr._reaper.is_alive()


# ---- B13：clock 參數 ----


def test_default_clock_is_monotonic():
    assert ModelManager(idle_seconds=TEN_MINUTES)._clock is time.monotonic


# ---- B11：載入模型時不持有全域 lock ----


def test_loading_one_model_does_not_block_other_keys(clock):
    import threading

    mgr = manager(clock)
    loading = threading.Event()
    release = threading.Event()

    def slow_loader():
        loading.set()
        assert release.wait(5)
        return "E"

    th = threading.Thread(target=lambda: mgr.use("embedding", slow_loader).__enter__())
    th.start()
    assert loading.wait(5)
    # embedding 還在載入中：其他 key 照常使用，loaded()／unload_idle() 也不會被卡住
    done = threading.Event()

    def other():
        with mgr.use("whisper", lambda: "W"):
            pass
        mgr.loaded()
        mgr.unload_idle()
        done.set()

    t2 = threading.Thread(target=other)
    t2.start()
    assert done.wait(2), "載入 embedding 時卡住了其他模型"
    release.set()
    th.join(5)
    t2.join(5)
    assert mgr.loaded() == ["embedding", "whisper"]


def test_same_key_loaded_only_once_concurrently(clock):
    import threading

    mgr = manager(clock)
    loads = []
    barrier = threading.Barrier(4)

    def loader():
        loads.append(1)
        time.sleep(0.05)
        return "W"

    def use():
        barrier.wait(5)
        with mgr.use("whisper", loader) as m:
            assert m == "W"

    threads = [threading.Thread(target=use) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert loads == [1]


def test_unload_all_keeps_given_key_and_in_use(clock):
    mgr = manager(clock)
    for k in ("a", "b"):
        with mgr.use(k, lambda: k):
            pass
    with mgr.use("c", lambda: "C"):
        assert mgr.unload_all(keep="a") == ["b"]  # c 使用中不卸載
    assert mgr.loaded() == ["a", "c"]


def test_reaper_releases_torch_cache_when_idle(clock, monkeypatch):
    """沒有模型使用中時，reaper 也會釋放 torch 快取（例如 OOM 失敗後殘留的配置）。"""
    emptied = []
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(
        is_available=lambda: True, empty_cache=lambda: emptied.append(1)))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    mgr = manager(clock, check_interval=0.01)
    mgr.start()
    try:
        deadline = time.monotonic() + 5
        while not emptied and time.monotonic() < deadline:
            time.sleep(0.01)
        assert emptied
        # 有模型使用中時不清
        with mgr.use("whisper", lambda: "W"):
            time.sleep(0.05)
            n = len(emptied)
            time.sleep(0.1)
            assert len(emptied) == n
    finally:
        mgr.stop()
        mgr._reaper.join(5)
