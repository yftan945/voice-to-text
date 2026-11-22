"""重跑：沿用同一筆紀錄，完成後取代舊逐字稿與編輯內容；音檔已刪除時回 409。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.jobs import queue
from app.main import create_app

from fakes import FakeBackend, patch_audio


@pytest.fixture()
def client(data_dir, monkeypatch):
    patch_audio(monkeypatch)
    app = create_app(backend=FakeBackend(), start_worker=False)
    with TestClient(app) as c:
        yield c


def upload(client, **form):
    files = {"file": ("會議.qta", b"fake-audio-bytes", "audio/mp4")}
    r = client.post("/api/jobs", files=files, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def run_next_job(client):
    job = queue.claim_next()
    assert job is not None
    client.app.state.worker.process(job)
    return queue.get(job["id"])


def done_job_with_edit(client):
    """完成一個任務並做過編輯（第三段原本是「人工只能很好」）。"""
    job = upload(client, diarize="false")
    run_next_job(client)
    r = client.post(f"/api/jobs/{job['id']}/transcript/edit",
                    json={"op": "set_text", "segment_id": 0, "text": "手動改過"})
    assert r.status_code == 200 and r.json()["edited"] is True
    return job


def test_rerun_reuses_record_and_replaces_transcript_and_edits(client):
    """換字典重跑：同一個 id、不新增紀錄；完成後原始逐字稿換新，舊的編輯清掉。"""
    job = done_job_with_edit(client)
    old = client.get(f"/api/jobs/{job['id']}/transcript").json()
    assert old["original"]["segments"][1]["text"] == "人工只能很好"

    d = client.post("/api/dictionaries", json={"name": "d", "content": "人工智慧 = 人工只能"}).json()
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={"dictionary_ids": [d["id"]], "diarize": False})
    assert r.status_code == 200
    assert r.json()["id"] == job["id"] and r.json()["status"] == "queued"
    assert [j["id"] for j in client.get("/api/jobs").json()] == [job["id"]]

    done = run_next_job(client)
    assert done["id"] == job["id"] and done["status"] == "done" and done["dictionary_ids"] == [d["id"]]
    t = client.get(f"/api/jobs/{job['id']}/transcript").json()
    assert t["edited"] is False
    assert t["original"]["segments"][1]["text"] == "人工智慧很好"
    assert t["current"]["segments"][0]["text"] != "手動改過"
    assert [j["id"] for j in client.get("/api/jobs").json()] == [job["id"]]


def test_old_transcript_kept_while_rerun_is_queued(client):
    """重跑排隊期間，舊的逐字稿與編輯仍可讀取（完成後才取代）。"""
    job = done_job_with_edit(client)
    client.post(f"/api/jobs/{job['id']}/rerun", json={"diarize": False})
    t = client.get(f"/api/jobs/{job['id']}/transcript")
    assert t.status_code == 200
    assert t.json()["edited"] is True and t.json()["current"]["segments"][0]["text"] == "手動改過"


def test_failed_rerun_keeps_old_transcript(client):
    """重跑失敗時不動舊的逐字稿與編輯內容。"""
    job = done_job_with_edit(client)
    client.post(f"/api/jobs/{job['id']}/rerun", json={"diarize": False})
    client.app.state.worker.backend = FakeBackend(transcribe_error=RuntimeError("壞掉"))
    failed = run_next_job(client)
    assert failed["status"] == "failed" and failed["has_transcript"] is True
    t = client.get(f"/api/jobs/{job['id']}/transcript").json()
    assert t["edited"] is True and t["current"]["segments"][0]["text"] == "手動改過"


def test_rerun_after_failure_clears_error(client):
    """之前失敗的任務重跑成功後，錯誤訊息清掉。"""
    client.app.state.worker.backend = FakeBackend(transcribe_error=RuntimeError("壞掉"))
    job = upload(client)
    assert run_next_job(client)["status"] == "failed"
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={})
    assert r.status_code == 200 and r.json()["error"] is None
    client.app.state.worker.backend = FakeBackend()
    done = run_next_job(client)
    assert done["status"] == "done" and done["error"] is None


@pytest.mark.parametrize("status", ["queued", "running"])
def test_rerun_refused_while_active(client, status):
    job = upload(client)
    if status == "running":
        queue.claim_next()
    assert client.post(f"/api/jobs/{job['id']}/rerun", json={}).status_code == 409


def test_rerun_409_when_audio_file_missing_on_disk(client):
    """has_audio 還是 true，但原始檔已不在磁碟上，也要回 409，且狀態不變。"""
    job = upload(client)
    run_next_job(client)
    storage.original_path(queue.get(job["id"])).unlink()
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={})
    assert r.status_code == 409 and "音檔已刪除" in r.json()["detail"]
    assert queue.get(job["id"])["status"] == "done"


def test_rerun_409_after_audio_deleted_keeps_transcript(client):
    """只刪音檔後重跑回 409，逐字稿保留、任務不會被重新排隊。"""
    job = upload(client)
    run_next_job(client)
    assert client.delete(f"/api/jobs/{job['id']}?mode=audio").status_code == 200
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={"model": "large-v3-turbo"})
    assert r.status_code == 409
    got = queue.get(job["id"])
    assert got["status"] == "done" and got["model"] == "large-v3"
    assert client.get(f"/api/jobs/{job['id']}/transcript").status_code == 200
    assert queue.claim_next() is None


def test_rerun_invalid_model_does_not_requeue(client):
    job = upload(client)
    run_next_job(client)
    assert client.post(f"/api/jobs/{job['id']}/rerun", json={"model": "nope"}).status_code == 400
    assert queue.get(job["id"])["status"] == "done"


def test_rerun_missing_job_404(client):
    assert client.post("/api/jobs/nope/rerun", json={}).status_code == 404


# ---- B7：重跑是條件式 UPDATE ----


def test_requeue_is_conditional(client):
    job = upload(client)
    assert queue.requeue(job["id"], model="large-v3", diarize=False, num_speakers=None, dictionary_ids=[]) is False
    queue.claim_next()
    assert queue.requeue(job["id"], model="large-v3", diarize=False, num_speakers=None, dictionary_ids=[]) is False
    queue.update(job["id"], status="done", has_audio=False)
    assert queue.requeue(job["id"], model="large-v3", diarize=False, num_speakers=None, dictionary_ids=[]) is False
    queue.update(job["id"], has_audio=True)
    assert queue.requeue(job["id"], model="large-v3", diarize=False, num_speakers=None, dictionary_ids=[]) is True


def test_rerun_race_returns_409(client, monkeypatch):
    """API 檢查完之後、更新之前，另一個請求先把任務重新排隊：回 409，不會重複排隊。"""
    from app.api import jobs as jobs_api

    job = upload(client)
    run_next_job(client)
    real = jobs_api._validate_options

    def validate_then_someone_else_reruns(*a, **kw):
        real(*a, **kw)
        queue.update(job["id"], status="queued")

    monkeypatch.setattr(jobs_api, "_validate_options", validate_then_someone_else_reruns)
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={})
    assert r.status_code == 409 and "排隊或執行中" in r.json()["detail"]
