"""批次上傳與佇列：依 position 領取、調整順序（含並發）、batch_id、整批取消、預估時間、migration。"""
from __future__ import annotations

import random
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db, storage
from app.config import settings
from app.jobs import estimate, queue
from app.main import create_app

from fakes import FakeBackend, patch_audio


def new(name, *, model="large-v3", diarize=True, duration=None, batch_id=None):
    job = queue.create(filename=name, model=model, diarize=diarize, num_speakers=None, dictionary_ids=[],
                       audio_ext=".wav", size_bytes=1, batch_id=batch_id)
    if duration is not None:
        queue.update(job["id"], duration=duration)
    return queue.get(job["id"])


def queued_ids():
    return [j["id"] for j in queue.list_active() if j["status"] == "queued"]


@pytest.fixture()
def client(data_dir, monkeypatch):
    patch_audio(monkeypatch, seconds=60.0)
    app = create_app(backend=FakeBackend(), start_worker=False)
    with TestClient(app) as c:
        yield c


def upload(client, name="a.m4a", **form):
    r = client.post("/api/jobs", files={"file": (name, b"fake-audio", "audio/mp4")}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


# ---- 排隊順序 ----


def test_claim_follows_position_not_upload_time(data_dir):
    a, b, c = new("a"), new("b"), new("c")
    assert [a["position"], b["position"], c["position"]] == [1, 2, 3]
    queue.move(c["id"], to="front")
    assert queue.claim_next()["id"] == c["id"]
    assert queue.claim_next()["id"] == a["id"]
    assert queue.claim_next()["id"] == b["id"]
    assert queue.claim_next() is None


def test_move_directions(data_dir):
    a, b, c, d = (new(x)["id"] for x in "abcd")
    assert queue.move(d, to="front") == [d, a, b, c]
    assert queue.move(d, to="down") == [a, d, b, c]
    assert queue.move(a, to="up") == [a, d, b, c]  # 已經在最前，不動
    assert queue.move(c, to="up") == [a, d, c, b]
    assert queue.move(a, to="back") == [d, c, b, a]
    assert queue.move(a, before=c) == [d, a, c, b]
    assert queue.move(d, after=b) == [a, c, b, d]
    assert queue.move(b, before=b) == [a, c, b, d]  # 拖到自己身上
    # 重新編號為 1..n
    assert [queue.get(x)["position"] for x in (a, c, b, d)] == [1, 2, 3, 4]
    with pytest.raises(ValueError):
        queue.move(a)
    with pytest.raises(ValueError):
        queue.move(a, to="front", before=b)


def test_running_and_finished_jobs_cannot_move(data_dir):
    a, b = new("a"), new("b")
    queue.claim_next()  # a 處理中
    with pytest.raises(queue.MoveError, match="處理中的任務不能移動"):
        queue.move(a["id"], to="back")
    # 不能排到處理中的任務前面
    with pytest.raises(queue.MoveError):
        queue.move(b["id"], before=a["id"])
    queue.update(a["id"], status="done")
    with pytest.raises(queue.MoveError, match="只有排隊中"):
        queue.move(a["id"], to="front")
    with pytest.raises(KeyError):
        queue.move("nope", to="front")


def test_new_and_rerun_go_to_back_restart_goes_to_front(data_dir):
    a, b = new("a"), new("b")
    queue.move(b["id"], to="front")  # b, a
    c = new("c")
    assert queued_ids() == [b["id"], a["id"], c["id"]]
    # 重新轉錄：排到最後
    first = queue.claim_next()
    assert first["id"] == b["id"]
    queue.update(b["id"], status="done")
    assert queue.requeue(b["id"], model="large-v3", diarize=True, num_speakers=None, dictionary_ids=[])
    assert queued_ids() == [a["id"], c["id"], b["id"]]
    # 容器重啟：處理到一半的任務放回最前面
    assert queue.claim_next()["id"] == a["id"]
    assert queue.requeue_unfinished() == 1
    assert queued_ids() == [a["id"], c["id"], b["id"]]
    assert queue.claim_next()["id"] == a["id"]


def test_concurrent_moves_keep_queue_consistent(data_dir):
    """多個執行緒同時調整順序、同時有 worker 領取：不會有重複順位、任務不會遺失或重複領取。"""
    jobs = [new(f"j{i}")["id"] for i in range(12)]
    errors: list[BaseException] = []
    claimed: list[str] = []
    start = threading.Barrier(7)

    def mover(seed):
        rnd = random.Random(seed)
        start.wait()
        for _ in range(25):
            jid = rnd.choice(jobs)
            kind = rnd.choice(["front", "back", "up", "down", "before", "after"])
            try:
                if kind in ("before", "after"):
                    queue.move(jid, **{kind: rnd.choice(jobs)})
                else:
                    queue.move(jid, to=kind)
            except queue.MoveError:
                pass  # 任務剛好被領走
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

    def claimer():
        start.wait()
        for _ in range(3):
            j = queue.claim_next()
            if j:
                claimed.append(j["id"])
            time.sleep(0.005)

    threads = [threading.Thread(target=mover, args=(i,)) for i in range(6)] + [threading.Thread(target=claimer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert len(claimed) == 3 and len(set(claimed)) == 3
    remaining = queued_ids()
    assert sorted(remaining + claimed) == sorted(jobs)
    positions = [queue.get(x)["position"] for x in remaining]
    assert len(set(positions)) == len(positions)
    assert positions == sorted(positions)


def test_concurrent_front_moves_via_api(client):
    ids = [upload(client, f"{i}.m4a")["id"] for i in range(6)]
    results: list[int] = []
    barrier = threading.Barrier(len(ids))

    def go(jid):
        barrier.wait()
        results.append(client.post(f"/api/jobs/{jid}/move", json={"to": "front"}).status_code)

    threads = [threading.Thread(target=go, args=(x,)) for x in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == [200] * len(ids)
    order = queued_ids()
    assert sorted(order) == sorted(ids)
    assert [queue.get(x)["position"] for x in order] == list(range(1, len(ids) + 1))


# ---- API ----


def test_move_api(client):
    a, b, c = (upload(client, f"{x}.m4a") for x in "abc")
    r = client.post(f"/api/jobs/{c['id']}/move", json={"to": "front"})
    assert r.status_code == 200
    body = r.json()
    assert body["order"] == [c["id"], a["id"], b["id"]]
    ranks = {j["id"]: j["queue_rank"] for j in body["jobs"]}
    assert ranks == {c["id"]: 1, a["id"]: 2, b["id"]: 3}
    r = client.post(f"/api/jobs/{a['id']}/move", json={"after": b["id"]})
    assert r.json()["order"] == [c["id"], b["id"], a["id"]]

    assert client.post(f"/api/jobs/{a['id']}/move", json={}).status_code == 400
    assert client.post(f"/api/jobs/{a['id']}/move", json={"to": "front", "after": b["id"]}).status_code == 400
    assert client.post(f"/api/jobs/{a['id']}/move", json={"to": "sideways"}).status_code == 422
    assert client.post("/api/jobs/nope/move", json={"to": "front"}).status_code == 404

    running = queue.claim_next()
    assert running["id"] == c["id"]
    r = client.post(f"/api/jobs/{c['id']}/move", json={"to": "back"})
    assert r.status_code == 409 and r.json()["detail"] == "處理中的任務不能移動"
    r = client.post(f"/api/jobs/{a['id']}/move", json={"before": c["id"]})
    assert r.status_code == 409
    # 順位：處理中的不算，排隊中從 1 開始；queue_position 是前面還有幾個（含處理中）
    jobs = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert jobs[c["id"]]["queue_rank"] is None and jobs[c["id"]]["eta_start_seconds"] == 0
    assert (jobs[b["id"]]["queue_rank"], jobs[b["id"]]["queue_position"]) == (1, 1)
    assert (jobs[a["id"]]["queue_rank"], jobs[a["id"]]["queue_position"]) == (2, 2)


def test_upload_with_batch_id(client):
    a = upload(client, "a.m4a", batch_id="batch_1-x")
    b = upload(client, "b.m4a", batch_id="batch_1-x")
    c = upload(client, "c.m4a")
    assert a["batch_id"] == b["batch_id"] == "batch_1-x"
    assert c["batch_id"] is None
    assert [j["id"] for j in queue.list_batch("batch_1-x")] == [a["id"], b["id"]]
    for bad in ("../x", "a b", "x" * 65):
        r = client.post("/api/jobs", files={"file": ("a.m4a", b"x", "audio/mp4")}, data={"batch_id": bad})
        assert r.status_code == 400
    # 空字串視同沒有批次
    assert upload(client, "d.m4a", batch_id="")["batch_id"] is None


def test_cancel_batch(client):
    a = upload(client, "a.m4a", batch_id="b1")
    b = upload(client, "b.m4a", batch_id="b1")
    c = upload(client, "c.m4a", batch_id="b1")
    other = upload(client, "other.m4a")
    # a 已完成、b 處理中、c 排隊中
    done = queue.claim_next()
    client.app.state.worker.process(done)
    assert queue.get(a["id"])["status"] == "done"
    assert queue.claim_next()["id"] == b["id"]

    r = client.post("/api/batches/b1/cancel")
    assert r.status_code == 200
    body = r.json()
    assert (body["canceled"], body["canceling"], body["total"]) == (1, 1, 3)
    assert {j["id"] for j in body["jobs"]} == {a["id"], b["id"], c["id"]}
    assert queue.get(a["id"])["status"] == "done"
    assert queue.get(b["id"])["cancel_requested"] is True
    assert queue.get(c["id"])["status"] == "canceled"
    assert queue.get(other["id"])["status"] == "queued"
    assert client.post("/api/batches/nope/cancel").status_code == 404


def test_rerun_keeps_batch_id(client):
    a = upload(client, "a.m4a", batch_id="b2")
    client.app.state.worker.process(queue.claim_next())
    r = client.post(f"/api/jobs/{a['id']}/rerun", json={"diarize": False})
    assert r.status_code == 200 and r.json()["batch_id"] == "b2"


# ---- 預估時間 ----


def test_estimate_without_history_uses_defaults(data_dir):
    a = new("a", model="large-v3", diarize=True, duration=2887)
    b = new("b", model="large-v3-turbo", diarize=False, duration=600)
    c = new("c", model="large-v3-turbo", diarize=False, duration=None)
    d = new("d", model="large-v3-turbo", diarize=False, duration=60)
    rates = estimate.load_rates()
    assert rates.samples == {}
    info = estimate.queue_info(queue.list_active(), rates=rates, loaded=[])

    ea = estimate.BASE_OVERHEAD + estimate.default_rate("large-v3", True) * 2887 + estimate.DEFAULT_LOAD_SECONDS
    # 48 分鐘、large-v3＋講者辨識：約 172 + 45 秒加上其他步驟與載入，大約 4 分多鐘
    assert 240 < ea < 300
    assert info[a["id"]]["estimate_seconds"] == estimate._round(ea)
    assert info[a["id"]]["eta_start_seconds"] == 0 and info[a["id"]]["queue_rank"] == 1
    assert info[a["id"]]["estimate_basis"] == "default"
    # 換模型：要再加一次載入時間
    eb = estimate.BASE_OVERHEAD + estimate.default_rate("large-v3-turbo", False) * 600 + estimate.DEFAULT_LOAD_SECONDS
    assert info[b["id"]]["eta_start_seconds"] == estimate._round(ea)
    assert info[b["id"]]["eta_finish_seconds"] == estimate._round(ea + eb)
    # 長度未知：自己和後面的任務都無法估算
    assert info[c["id"]]["estimate_seconds"] is None and info[c["id"]]["eta_finish_seconds"] is None
    assert info[d["id"]]["eta_start_seconds"] is None
    assert info[d["id"]]["estimate_seconds"] is not None


def test_estimate_running_job_and_loaded_model(data_dir):
    a = new("a", model="large-v3", diarize=False, duration=1000)
    b = new("b", model="large-v3", diarize=False, duration=1000)
    queue.claim_next()
    queue.update(a["id"], progress=0.5)
    rates = estimate.Rates()
    info = estimate.queue_info(queue.list_active(), rates=rates, loaded=["whisper:large-v3"])
    est = estimate.BASE_OVERHEAD + estimate.default_rate("large-v3", False) * 1000
    assert info[a["id"]]["queue_rank"] is None
    assert info[a["id"]]["eta_finish_seconds"] == estimate._round(est * 0.5)
    # 同一個模型接著跑，不用再載入
    assert info[b["id"]]["queue_rank"] == 1 and info[b["id"]]["queue_position"] == 1
    assert info[b["id"]]["eta_finish_seconds"] == estimate._round(est * 0.5 + est)
    # 處理中的任務模型還在載入：剩餘時間加上載入時間
    loading = estimate.queue_info(queue.list_active(), rates=rates, loaded=[])
    assert loading[a["id"]]["eta_finish_seconds"] == estimate._round(est * 0.5 + rates.load_seconds)


def test_estimate_first_job_skips_load_when_model_loaded(data_dir):
    a = new("a", model="large-v3", diarize=False, duration=600)
    est = estimate.BASE_OVERHEAD + estimate.default_rate("large-v3", False) * 600
    rates = estimate.Rates()
    cold = estimate.queue_info(queue.list_active(), rates=rates, loaded=[])[a["id"]]
    warm = estimate.queue_info(queue.list_active(), rates=rates, loaded=["whisper:large-v3"])[a["id"]]
    assert warm["estimate_seconds"] == estimate._round(est)
    assert cold["estimate_seconds"] == estimate._round(est + estimate.DEFAULT_LOAD_SECONDS)


def _finished(name, *, model, diarize, duration, process, load=0.0):
    j = new(name, model=model, diarize=diarize, duration=duration)
    queue.update(j["id"], status="done", started_at=time.time() - process - load, finished_at=time.time(),
                 process_seconds=process, load_seconds=load)
    return j


def test_estimate_learns_from_history(data_dir):
    # 這台機器實際比預設慢：每秒音檔 0.2 秒
    for i in range(3):
        _finished(f"h{i}", model="large-v3", diarize=True, duration=1800,
                  process=estimate.BASE_OVERHEAD + 0.2 * 1800, load=12.0)
    # 太短的片段不拿來修正
    _finished("short", model="large-v3", diarize=True, duration=5, process=50)
    # 其他模型的紀錄不影響
    _finished("turbo", model="large-v3-turbo", diarize=True, duration=1800, process=1.0)
    rates = estimate.load_rates()
    r = rates.rate("large-v3", True)
    prior = estimate.default_rate("large-v3", True)
    expected = (0.2 * 5400 + prior * estimate.PRIOR_AUDIO_SECONDS) / (5400 + estimate.PRIOR_AUDIO_SECONDS)
    assert r == pytest.approx(expected)
    assert prior < r < 0.2
    assert rates.has_history("large-v3", True) and not rates.has_history("large-v3", False)
    assert rates.load_seconds == pytest.approx(12.0)
    # 沒有紀錄的組合仍用預設
    assert rates.rate("breeze-asr-25", False) == pytest.approx(estimate.default_rate("breeze-asr-25", False))

    job = new("next", model="large-v3", diarize=True, duration=600)
    info = estimate.queue_info(queue.list_active(), rates=rates, loaded=["whisper:large-v3"])[job["id"]]
    assert info["estimate_basis"] == "history"
    assert info["estimate_seconds"] == estimate._round(estimate.BASE_OVERHEAD + r * 600)


def test_estimate_history_keeps_recent_per_key(data_dir):
    rows = [{"model": "large-v3", "diarize": 1, "duration": 100.0, "process_seconds": 100.0 + estimate.BASE_OVERHEAD,
             "load_seconds": 0}] * (estimate.HISTORY_PER_KEY + 5)
    rates = estimate.rates_from_history(rows)
    work, audio, n = rates.samples[("large-v3", True)]
    assert n == estimate.HISTORY_PER_KEY and audio == 100.0 * estimate.HISTORY_PER_KEY
    assert rates.load_seconds == estimate.DEFAULT_LOAD_SECONDS


def test_worker_records_process_and_load_seconds(client):
    job = upload(client)
    client.app.state.worker.process(queue.claim_next())
    got = queue.get(job["id"])
    assert got["status"] == "done"
    assert got["process_seconds"] is not None and got["process_seconds"] >= 0
    assert got["load_seconds"] == 0  # 假後端不載入模型


def test_api_exposes_estimates(client):
    a = upload(client, "a.m4a", model="large-v3-turbo")
    b = upload(client, "b.m4a", model="large-v3-turbo")
    got = client.get(f"/api/jobs/{b['id']}").json()
    assert got["queue_rank"] == 2 and got["estimate_basis"] == "default"
    assert got["eta_start_seconds"] is not None and got["eta_finish_seconds"] > got["eta_start_seconds"]
    assert a["queue_rank"] == 1
    # 完成的任務沒有預估欄位
    client.app.state.worker.process(queue.claim_next())
    done = client.get(f"/api/jobs/{a['id']}").json()
    assert done["queue_rank"] is None and done["eta_finish_seconds"] is None


# ---- migration ----


V1_JOBS = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at REAL NOT NULL,
    queued_at REAL NOT NULL DEFAULT 0, started_at REAL, finished_at REAL, status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '', progress REAL NOT NULL DEFAULT 0, error TEXT,
    warnings TEXT NOT NULL DEFAULT '[]', model TEXT NOT NULL, diarize INTEGER NOT NULL DEFAULT 1,
    num_speakers INTEGER, dictionary_ids TEXT NOT NULL DEFAULT '[]', audio_ext TEXT NOT NULL DEFAULT '',
    has_audio INTEGER NOT NULL DEFAULT 1, has_playback INTEGER NOT NULL DEFAULT 0,
    has_transcript INTEGER NOT NULL DEFAULT 0, duration REAL, size_bytes INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_jobs_status ON jobs(status, queued_at);
"""


def test_migration_adds_queue_columns_and_keeps_order(tmp_path, monkeypatch):
    """目前正式環境的資料庫（有 attempts、沒有 position／batch_id）：補欄位後排隊順序維持原本的 queued_at 順序。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "v1")
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(V1_JOBS)
    rows = [("done1", 1.0, "done"), ("q3", 30.0, "queued"), ("run", 5.0, "running"), ("q1", 10.0, "queued"),
            ("q2", 20.0, "queued")]
    for jid, qa, st in rows:
        conn.execute("INSERT INTO jobs (id, filename, created_at, queued_at, status, model) VALUES (?, ?, ?, ?, ?, ?)",
                     (jid, f"{jid}.m4a", qa, qa, st, "large-v3"))
    conn.commit()
    conn.close()

    db.init_db()
    db.init_db()  # 重複執行不會出錯，也不會重新編號

    with db.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        indexes = {r["name"] for r in c.execute("PRAGMA index_list(jobs)")}
    assert {"position", "batch_id", "process_seconds", "load_seconds"} <= cols
    assert {"idx_jobs_queue", "idx_jobs_batch"} <= indexes
    assert queue.get("done1")["position"] == 0 and queue.get("done1")["batch_id"] is None
    assert [queue.get(x)["position"] for x in ("run", "q1", "q2", "q3")] == [1, 2, 3, 4]
    assert queued_ids() == ["q1", "q2", "q3"]
    # 新任務排在最後
    j = new("new")
    assert queued_ids() == ["q1", "q2", "q3", j["id"]]
    assert queue.claim_next()["id"] == "q1"


def test_fresh_database_has_new_columns(data_dir):
    with db.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
    assert {"position", "batch_id", "process_seconds", "load_seconds"} <= cols


def test_delete_all_removes_batch_job(client):
    a = upload(client, "a.m4a", batch_id="b3")
    queue.request_cancel(a["id"])
    assert client.delete(f"/api/jobs/{a['id']}?mode=all").status_code == 200
    assert queue.list_batch("b3") == []
    assert not storage.job_dir(a["id"]).exists()


def test_load_timer_counts_only_this_thread():
    from app.pipeline.models import ModelManager, load_timer, reset_load_timer

    mgr = ModelManager(idle_seconds=600)
    reset_load_timer()

    def slow():
        time.sleep(0.05)
        return object()

    with mgr.use("m", slow):
        pass
    with mgr.use("m", slow):  # 已載入，不再計時
        pass
    assert 0.05 <= load_timer() < 0.5

    other: list[float] = []

    def in_thread():
        reset_load_timer()
        other.append(load_timer())

    t = threading.Thread(target=in_thread)
    t.start()
    t.join()
    assert other == [0.0] and load_timer() >= 0.05
