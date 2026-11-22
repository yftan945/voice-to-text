"""GPU worker：容器重啟重新排隊、一次只跑一個任務、取消排隊中／執行中的任務、CUDA OOM。"""
from __future__ import annotations

import threading
import time

import pytest

from app import storage
from app.jobs import queue
from app.jobs.worker import Worker
from app.pipeline import align as align_mod
from app.pipeline import asr as asr_mod
from app.pipeline.backend import GpuBackend
from app.pipeline.errors import GPU_BUSY_MESSAGE
from app.pipeline.models import ModelManager

from fakes import FakeBackend, patch_audio, zh

OOM = "CUDA failed with error out of memory"
WAIT = 10.0  # 等待背景執行緒的上限（秒），正常情況下遠低於這個值


def make_job(name="a.m4a", *, diarize=False):
    """建立排隊中的任務，並放好原始音檔（worker 會檢查檔案存在）。"""
    job = queue.create(filename=name, model="large-v3", diarize=diarize, num_speakers=None,
                       dictionary_ids=[], audio_ext=".m4a", size_bytes=3)
    storage.job_dir(job["id"]).mkdir(parents=True, exist_ok=True)
    storage.original_path(job).write_bytes(b"abc")
    return job


def wait_for(pred, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def status(job_id):
    return queue.get(job_id)["status"]


class RecordingWorker(Worker):
    """記錄 worker 處理任務的順序。"""

    def __init__(self, backend, **kw):
        super().__init__(backend, **kw)
        self.order: list[str] = []

    def process(self, job):
        self.order.append(job["id"])
        super().process(job)


class BlockingBackend(FakeBackend):
    """轉錄時卡住，直到測試放行；同時記錄同時執行中的轉錄數量。"""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.transcribe_count = 0

    def transcribe(self, audio, model, hint_terms, duration, on_progress, check_cancel, on_dropped=None):
        with self._lock:
            self.active += 1
            self.transcribe_count += 1
            self.max_active = max(self.max_active, self.active)
        self.entered.release()
        try:
            assert self.release.wait(WAIT), "測試沒有放行轉錄"
            return super().transcribe(audio, model, hint_terms, duration, on_progress, check_cancel)
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture()
def run_worker(data_dir, monkeypatch):
    """啟動真的 worker 執行緒，測試結束時停止並等它結束。"""
    patch_audio(monkeypatch)
    started: list[Worker] = []

    def start(worker):
        worker.start()
        started.append(worker)
        return worker

    yield start
    for w in started:
        backend = w.backend
        if isinstance(backend, BlockingBackend):
            backend.release.set()
        w.stop()
        w._thread.join(WAIT)
        assert not w._thread.is_alive(), "worker 執行緒沒有停止"


# ---- 容器重啟 ----


def test_requeue_unfinished_only_touches_running(data_dir):
    """重新排隊只影響 running；queued／done／failed／canceled 原樣保留，running 的進度歸零。"""
    running = make_job("running")
    queue.claim_next()
    queue.update(running["id"], progress=0.5, stage="語音轉文字中")
    others = {}
    for st in ("done", "failed", "canceled"):
        j = make_job(st)
        queue.update(j["id"], status=st, stage=st)
        others[j["id"]] = st
    waiting = make_job("queued")

    assert queue.requeue_unfinished() == 1
    r = queue.get(running["id"])
    assert r["status"] == "queued" and r["progress"] == 0
    assert r["stage"] == {"code": "stage.requeued", "params": {}} and "重新排隊" in zh(r["stage"])
    for jid, st in others.items():
        assert status(jid) == st
    assert status(waiting["id"]) == "queued"
    # 沒有 running 的任務時不做事
    assert queue.requeue_unfinished() == 0


def test_worker_start_requeues_running_job_and_runs_it_first(run_worker):
    """模擬容器在任務執行中重啟：worker 啟動時把 running 放回佇列，而且維持原本的排隊順序先跑它。"""
    interrupted = make_job("interrupted")
    time.sleep(0.01)
    later = make_job("later")
    assert queue.claim_next()["id"] == interrupted["id"]  # 重啟前正在跑
    assert status(interrupted["id"]) == "running"

    worker = run_worker(RecordingWorker(FakeBackend(), poll_interval=0.05))
    assert wait_for(lambda: status(interrupted["id"]) == "done" and status(later["id"]) == "done")
    assert worker.order == [interrupted["id"], later["id"]]
    assert queue.get(interrupted["id"])["has_transcript"] is True


# ---- 一次只跑一個 ----


def test_worker_runs_one_job_at_a_time(run_worker):
    """第一個任務執行中時，第二個任務維持排隊；兩個任務的轉錄不會同時進行。"""
    backend = BlockingBackend()
    a = make_job("a")
    time.sleep(0.01)
    b = make_job("b")
    worker = run_worker(Worker(backend, poll_interval=0.05))

    assert backend.entered.acquire(timeout=WAIT)
    assert worker.current_job == a["id"]
    assert status(a["id"]) == "running"
    assert status(b["id"]) == "queued"
    assert queue.queue_position(b["id"]) == 1  # 前面有一個執行中
    # 還沒放行前不會開始第二個
    assert not backend.entered.acquire(timeout=0.3)
    assert status(b["id"]) == "queued"

    backend.release.set()
    assert wait_for(lambda: status(a["id"]) == "done" and status(b["id"]) == "done")
    assert backend.max_active == 1
    assert backend.transcribe_count == 2


def test_claim_next_concurrent_never_double_claims(data_dir):
    """多個執行緒同時領取佇列：每個任務只會被領取一次（BEGIN IMMEDIATE 交易）。"""
    ids = {make_job(f"j{i}")["id"] for i in range(8)}
    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def grab():
        barrier.wait(WAIT)
        while True:
            job = queue.claim_next()
            if job is None:
                return
            with lock:
                claimed.append(job["id"])

    threads = [threading.Thread(target=grab) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)
    assert sorted(claimed) == sorted(ids)  # 沒有重複、沒有漏掉


# ---- 取消 ----


def test_cancel_queued_job_is_never_run(run_worker):
    """執行中的任務之外，排隊中的任務被取消後，worker 不會去跑它。"""
    backend = BlockingBackend()
    a = make_job("a")
    time.sleep(0.01)
    b = make_job("b")
    run_worker(Worker(backend, poll_interval=0.05))
    assert backend.entered.acquire(timeout=WAIT)

    assert queue.request_cancel(b["id"]) == "canceled"
    backend.release.set()
    assert wait_for(lambda: status(a["id"]) == "done")
    # 給 worker 一點時間：若有 bug 會把 b 領出來跑
    assert not backend.entered.acquire(timeout=0.3)
    got = queue.get(b["id"])
    assert got["status"] == "canceled" and got["started_at"] is None
    assert backend.transcribe_count == 1


def test_cancel_running_job_stops_at_checkpoint(data_dir, monkeypatch):
    """執行中取消：設旗標，worker 在下一個檢查點停止，標成 canceled 並清掉旗標，不寫入逐字稿。"""
    patch_audio(monkeypatch)
    job = make_job()

    class CancelDuringTranscribe(FakeBackend):
        def transcribe(self, audio, model, hint_terms, duration, on_progress, check_cancel, on_dropped=None):
            assert queue.request_cancel(job["id"]) == "canceling"
            assert status(job["id"]) == "running"  # 旗標設好但還沒停
            return super().transcribe(audio, model, hint_terms, duration, on_progress, check_cancel)

    backend = CancelDuringTranscribe()
    Worker(backend).process(queue.claim_next())
    got = queue.get(job["id"])
    assert got["status"] == "canceled" and zh(got["stage"]) == "已取消"
    assert got["cancel_requested"] is False
    assert got["has_transcript"] is False and storage.load(job["id"]) is None
    assert not any(c[0] == "align" for c in backend.calls)  # 停在轉錄後的檢查點


def test_cancel_running_job_via_worker_thread(run_worker):
    """真的 worker 執行緒：執行中的任務被取消後變成 canceled，下一個任務照常執行。"""
    backend = BlockingBackend()
    a = make_job("a")
    time.sleep(0.01)
    b = make_job("b")
    run_worker(Worker(backend, poll_interval=0.05))
    assert backend.entered.acquire(timeout=WAIT)

    assert queue.request_cancel(a["id"]) == "canceling"
    backend.release.set()
    assert wait_for(lambda: status(a["id"]) == "canceled" and status(b["id"]) == "done")
    assert queue.get(a["id"])["cancel_requested"] is False


def test_cancel_finished_job_is_noop(data_dir):
    """已完成的任務取消不會改狀態。"""
    job = make_job()
    queue.update(job["id"], status="done")
    assert queue.request_cancel(job["id"]) == "done"
    assert status(job["id"]) == "done"
    assert queue.request_cancel("nope") is None


# ---- CUDA OOM ----


def test_worker_marks_gpu_busy_on_oom(data_dir, monkeypatch):
    """runner 回報 OOM 時，任務標成失敗，訊息就是「GPU 忙碌，請稍後重試」。"""
    patch_audio(monkeypatch)
    job = make_job()
    backend = FakeBackend(transcribe_error=RuntimeError(OOM))
    Worker(backend).process(queue.claim_next())
    got = queue.get(job["id"])
    assert got["status"] == "failed"
    assert got["error"] == {"code": "gpu.busy", "params": {}}
    assert zh(got["error"]) == GPU_BUSY_MESSAGE == "GPU 忙碌，請稍後重試"
    assert backend.unloaded


def patch_gpu_steps(monkeypatch, transcribe_impl):
    """讓 GpuBackend 不載入真的模型：換掉 asr／align 的載入與推論函式。"""
    monkeypatch.setattr(asr_mod, "load_whisper", lambda opt: f"whisper-{opt.id}")
    monkeypatch.setattr(asr_mod, "transcribe", transcribe_impl)
    monkeypatch.setattr(align_mod, "load_aligner", lambda lang: f"aligner-{lang}")
    monkeypatch.setattr(align_mod, "align",
                        lambda aligner, segs, audio, lang: [{**s, "words": []} for s in segs])


def test_gpu_backend_oom_once_unloads_idle_models_and_retries(data_dir, monkeypatch):
    """第一次 OOM：先卸載閒置的其他模型，再重試同一步驟；重試成功則任務完成。"""
    patch_audio(monkeypatch)
    manager = ModelManager(idle_seconds=600)
    with manager.use("diarization", lambda: "D"):
        pass  # 另一個已載入但閒置的模型
    calls: list[list[str]] = []

    def transcribe(model, audio, **kw):
        calls.append(manager.loaded())
        if len(calls) == 1:
            raise RuntimeError(OOM)
        return [{"start": 0.0, "end": 1.0, "text": "你好"}], "zh"

    patch_gpu_steps(monkeypatch, transcribe)
    job = make_job()
    Worker(GpuBackend(manager)).process(queue.claim_next())

    assert len(calls) == 2
    assert "diarization" in calls[0]
    assert "diarization" not in calls[1]  # 重試前已卸載閒置模型
    got = queue.get(job["id"])
    assert got["status"] == "done" and got["error"] is None
    assert storage.load(job["id"])[0]["segments"][0]["text"] == "你好"


def test_gpu_backend_second_oom_fails_with_gpu_busy(data_dir, monkeypatch):
    """重試後仍 OOM：只重試一次（共呼叫兩次），任務標失敗並顯示 GPU 忙碌。"""
    patch_audio(monkeypatch)
    manager = ModelManager(idle_seconds=600)
    calls = []

    def transcribe(model, audio, **kw):
        calls.append(1)
        raise RuntimeError(OOM)

    patch_gpu_steps(monkeypatch, transcribe)
    job = make_job()
    Worker(GpuBackend(manager)).process(queue.claim_next())

    assert len(calls) == 2
    got = queue.get(job["id"])
    assert got["status"] == "failed" and zh(got["error"]) == GPU_BUSY_MESSAGE
    assert manager.loaded() == []  # 失敗後模型都已卸載


def test_gpu_backend_oom_in_align_also_retried(data_dir, monkeypatch):
    """OOM 重試套用在每個步驟（這裡是逐字對齊），不只轉錄。"""
    patch_audio(monkeypatch)
    manager = ModelManager(idle_seconds=600)
    patch_gpu_steps(monkeypatch, lambda model, audio, **kw: ([{"start": 0.0, "end": 1.0, "text": "你好"}], "zh"))
    align_calls = []

    def align(aligner, segs, audio, lang):
        align_calls.append(1)
        if len(align_calls) == 1:
            raise RuntimeError(OOM)
        return [{**s, "words": []} for s in segs]

    monkeypatch.setattr(align_mod, "align", align)
    job = make_job()
    Worker(GpuBackend(manager)).process(queue.claim_next())
    assert len(align_calls) == 2
    got = queue.get(job["id"])
    assert got["status"] == "done"
    assert not any("逐字對齊失敗" in w for w in got["warnings"])


def test_gpu_backend_non_oom_error_is_not_retried(data_dir, monkeypatch):
    """不是 OOM 的錯誤不重試，任務失敗並保留原始錯誤訊息（不會誤報 GPU 忙碌）。"""
    patch_audio(monkeypatch)
    manager = ModelManager(idle_seconds=600)
    calls = []

    def transcribe(model, audio, **kw):
        calls.append(1)
        raise ValueError("壞掉的音訊")

    patch_gpu_steps(monkeypatch, transcribe)
    job = make_job()
    Worker(GpuBackend(manager)).process(queue.claim_next())
    assert len(calls) == 1
    got = queue.get(job["id"])
    assert got["status"] == "failed"
    assert "壞掉的音訊" in got["error"] and got["error"] != GPU_BUSY_MESSAGE


# ---- B2：GpuBusy 失敗後在 except 外釋放 GPU 記憶體 ----


def test_worker_frees_gpu_memory_after_gpu_busy_outside_except(data_dir, monkeypatch):
    import sys

    from app.jobs import worker as worker_mod

    patch_audio(monkeypatch)
    job = make_job()
    seen = []
    monkeypatch.setattr(worker_mod, "free_gpu_memory", lambda: seen.append(sys.exc_info()[0]))
    Worker(FakeBackend(transcribe_error=RuntimeError(OOM))).process(queue.claim_next())
    assert zh(queue.get(job["id"])["error"]) == GPU_BUSY_MESSAGE
    assert seen == [None]  # 呼叫時已不在 except 區塊裡


# ---- B5：worker 不會因為資料庫錯誤死掉、任務不會卡在 running ----


def test_worker_loop_survives_claim_errors(run_worker, monkeypatch):
    job = make_job()
    real_claim = queue.claim_next
    failures = [0]

    def flaky_claim():
        if failures[0] < 2:
            failures[0] += 1
            raise RuntimeError("database is locked")
        return real_claim()

    monkeypatch.setattr(queue, "claim_next", flaky_claim)
    worker = run_worker(Worker(FakeBackend(), poll_interval=0.05))
    assert wait_for(lambda: status(job["id"]) == "done")
    assert failures[0] == 2 and worker.is_alive()


def test_unexpected_error_after_run_does_not_leave_job_running(data_dir, monkeypatch):
    """runner 成功但寫入逐字稿時出錯：finally 把任務標成失敗，不會卡在 running。"""
    patch_audio(monkeypatch)
    job = make_job()

    def boom(job_id, transcript):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "save_original", boom)
    Worker(FakeBackend()).process(queue.claim_next())
    got = queue.get(job["id"])
    assert got["status"] == "failed" and "未預期" in zh(got["error"])


# ---- B6：重新排隊次數上限 ----


def test_requeue_unfinished_gives_up_after_two_restarts(data_dir):
    job = make_job()
    for expected in (1, 2):
        assert queue.claim_next()["id"] == job["id"]
        assert queue.requeue_unfinished() == 1
        got = queue.get(job["id"])
        assert got["status"] == "queued" and got["attempts"] == expected
    # 第三次中斷：不再重新排隊，標為失敗
    queue.claim_next()
    assert queue.requeue_unfinished() == 0
    got = queue.get(job["id"])
    assert got["status"] == "failed"
    assert zh(got["error"]) == "處理時程式中斷多次，可能是檔案過長或損壞"
    assert queue.claim_next() is None


def test_user_rerun_resets_attempts(data_dir):
    job = make_job()
    queue.claim_next()
    queue.requeue_unfinished()
    assert queue.get(job["id"])["attempts"] == 1
    queue.update(job["id"], status="failed")
    assert queue.requeue(job["id"], model="large-v3", diarize=False, num_speakers=None, dictionary_ids=[])
    assert queue.get(job["id"])["attempts"] == 0


def test_attempts_migration_on_old_database(tmp_path, monkeypatch):
    """舊資料庫沒有 attempts 欄位：init_db 會補上，既有資料保留、預設 0。"""
    import sqlite3

    from app import db
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "old")
    settings.ensure_dirs()
    # 拿掉之後才加的欄位，模擬最早版本的資料庫
    added = ("attempts", "position", "batch_id", "process_seconds", "load_seconds",
             "-- 來源", "source", "video_id", "yt_", "use_captions", "caption_source", "download_",
             "speaker_summary")
    old_schema = "\n".join(line for line in db.SCHEMA.splitlines() if not line.strip().startswith(added))
    old_schema = old_schema.replace("cancel_requested INTEGER NOT NULL DEFAULT 0,", "cancel_requested INTEGER NOT NULL DEFAULT 0")
    assert "attempts" not in old_schema and "position" not in old_schema
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(old_schema)
    conn.execute("INSERT INTO jobs (id, filename, created_at, status, model) VALUES ('old', 'a.m4a', 1, 'done', 'large-v3')")
    conn.commit()
    conn.close()

    db.init_db()
    db.init_db()  # 重複執行不會出錯
    got = queue.get("old")
    assert got["attempts"] == 0 and got["status"] == "done"
