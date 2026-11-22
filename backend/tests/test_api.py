"""API 端對端（假的推論後端）：上傳 → 跑任務 → 結果 → 編輯 → 匯出 → 三種刪除。"""
import pytest
from fastapi.testclient import TestClient

from app import storage
from app.config import settings
from app.jobs import queue
from app.main import create_app

from fakes import FakeBackend, patch_audio


@pytest.fixture()
def client(data_dir, monkeypatch):
    patch_audio(monkeypatch)
    app = create_app(backend=FakeBackend(), start_worker=False)
    with TestClient(app) as c:
        yield c


def run_next_job(client):
    job = queue.claim_next()
    assert job is not None
    client.app.state.worker.process(job)
    return queue.get(job["id"])


def upload(client, **form):
    files = {"file": ("會議.qta", b"fake-audio-bytes", "audio/mp4")}
    r = client.post("/api/jobs", files=files, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def test_models_endpoint(client):
    data = client.get("/api/models").json()
    ids = [m["id"] for m in data["models"]]
    assert ids == ["breeze-asr-25", "large-v3-turbo", "large-v3"]
    labels = {m["id"]: m["label"] for m in data["models"]}
    assert labels == {"breeze-asr-25": "精準（Breeze-ASR-25）", "large-v3-turbo": "快速（Whisper large-v3-turbo）",
                      "large-v3": "多語（Whisper large-v3）"}
    breeze = data["models"][0]
    assert breeze["available"] is False  # 測試環境沒有轉好的 CTranslate2 目錄
    # 設定的預設是 Breeze，但還沒安裝時改用 large-v3
    assert settings.default_model == "breeze-asr-25"
    assert data["default"] == "large-v3"


def test_default_model_is_breeze_when_installed(client, tmp_path, monkeypatch):
    breeze_dir = tmp_path / "breeze"
    breeze_dir.mkdir()
    (breeze_dir / "model.bin").write_bytes(b"x")
    monkeypatch.setattr(settings, "breeze_model_path", str(breeze_dir))
    assert client.get("/api/models").json()["default"] == "breeze-asr-25"
    job = upload(client)  # 沒指定模型：用預設
    assert job["model"] == "breeze-asr-25"


def test_full_flow(client):
    d = client.post("/api/dictionaries", json={"name": "公司", "content": "人工智慧 = 人工只能"}).json()
    assert d["rule_count"] == 1
    job = upload(client, model="large-v3", diarize="true", dictionary_ids=str(d["id"]))
    assert job["status"] == "queued" and job["queue_position"] == 0
    assert job["dictionary_ids"] == [d["id"]]

    done = run_next_job(client)
    assert done["status"] == "done" and done["has_transcript"] and done["has_playback"]

    # SSE：任務已完成，送出一個 job 事件後關閉
    with client.stream("GET", f"/api/jobs/{job['id']}/events") as r:
        body = "".join(r.iter_text())
    assert "event: job" in body and '"status": "done"' in body

    t = client.get(f"/api/jobs/{job['id']}/transcript").json()
    assert t["edited"] is False
    assert "speaker_embeddings" not in t["original"]
    assert t["voiceprint_speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert t["current"]["segments"][2]["text"] == "人工智慧很好"

    # 編輯：改名、改字、改單段講者
    r = client.post(f"/api/jobs/{job['id']}/transcript/edit",
                    json={"op": "rename_speaker", "speaker": "SPEAKER_00", "name": "Alex"})
    assert r.status_code == 200 and r.json()["edited"] is True
    client.post(f"/api/jobs/{job['id']}/transcript/edit",
                json={"op": "set_text", "segment_id": 0, "text": "各位好"})
    bad = client.post(f"/api/jobs/{job['id']}/transcript/edit", json={"op": "set_text", "segment_id": 99,
                                                                      "text": "x"})
    assert bad.status_code == 400

    # 原始結果不受影響
    t = client.get(f"/api/jobs/{job['id']}/transcript").json()
    assert t["original"]["segments"][0]["text"] == "大家好"
    assert t["current"]["segments"][0]["text"] == "各位好"

    # 匯出用編輯後版本
    srt = client.get(f"/api/jobs/{job['id']}/export/srt")
    assert srt.status_code == 200
    assert srt.text.startswith("1\n00:00:00,000 --> ")
    assert "Alex：各位好" in srt.text
    assert "attachment" in srt.headers["content-disposition"]
    for fmt in ("txt", "md", "vtt", "json"):
        assert client.get(f"/api/jobs/{job['id']}/export/{fmt}").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}/export/docx").status_code == 400

    # 錯字 → 正字加入字典
    d2 = client.post(f"/api/dictionaries/{d['id']}/rules", json={"wrong": "大家", "correct": "各位"}).json()
    assert "各位 = 大家" in d2["content"]

    # 從講者存成聲紋
    r = client.post(f"/api/jobs/{job['id']}/speakers/SPEAKER_01/voiceprint", json={"name": "Bob"})
    assert r.status_code == 200
    assert r.json()["current"]["speakers"]["SPEAKER_01"]["name"] == "Bob"
    vps = client.get("/api/voiceprints").json()
    assert [v["name"] for v in vps] == ["Bob"] and "embedding" not in vps[0]

    # 重設編輯
    t = client.post(f"/api/jobs/{job['id']}/transcript/reset").json()
    assert t["edited"] is False and t["current"]["segments"][0]["text"] == "大家好"


def test_delete_modes_and_rerun(client):
    job = upload(client)
    # 排隊中不能刪
    assert client.delete(f"/api/jobs/{job['id']}?mode=all").status_code == 409
    run_next_job(client)

    r = client.delete(f"/api/jobs/{job['id']}?mode=transcript")
    assert r.status_code == 200 and r.json()["job"]["has_transcript"] is False
    assert client.get(f"/api/jobs/{job['id']}/transcript").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/audio").status_code == 200

    # 保留音檔 → 可換模型重跑
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={"model": "large-v3-turbo", "diarize": False})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    done = run_next_job(client)
    assert done["model"] == "large-v3-turbo" and done["has_transcript"]

    r = client.delete(f"/api/jobs/{job['id']}?mode=audio")
    assert r.json()["job"]["has_audio"] is False
    assert client.get(f"/api/jobs/{job['id']}/audio").status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/transcript").status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/rerun", json={}).status_code == 409

    assert client.delete(f"/api/jobs/{job['id']}?mode=all").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert not storage.job_dir(job["id"]).exists()
    assert client.delete(f"/api/jobs/{job['id']}?mode=bogus").status_code == 422


def test_upload_validation(client):
    files = {"file": ("a.wav", b"x", "audio/wav")}
    assert client.post("/api/jobs", files=files, data={"model": "nope"}).status_code == 400
    assert client.post("/api/jobs", files=files, data={"model": "breeze-asr-25"}).status_code == 400
    assert client.post("/api/jobs", files=files, data={"num_speakers": "50"}).status_code == 400
    empty = {"file": ("a.wav", b"", "audio/wav")}
    assert client.post("/api/jobs", files=empty).status_code == 400


def test_cancel_queued(client):
    job = upload(client)
    r = client.post(f"/api/jobs/{job['id']}/cancel").json()
    assert r["status"] == "canceled"
    assert queue.claim_next() is None


def test_voiceprint_enroll(client, monkeypatch):
    files = {"file": ("short.m4a", b"abc", "audio/mp4")}
    assert client.post("/api/voiceprints", files=files, data={"name": "我"}).status_code == 400  # 4 秒太短
    patch_audio(monkeypatch, seconds=12.0)
    files = {"file": ("me.m4a", b"abc", "audio/mp4")}
    r = client.post("/api/voiceprints", files=files, data={"name": "我"})
    assert r.status_code == 200, r.text
    vp = r.json()
    assert vp["name"] == "我" and vp["compatible"] is False  # FakeBackend 的模型名稱和設定不同
    assert client.patch(f"/api/voiceprints/{vp['id']}", json={"name": "Alex"}).status_code == 200
    assert client.delete(f"/api/voiceprints/{vp['id']}").status_code == 200


def test_dictionary_crud(client):
    d = client.post("/api/dictionaries", json={"name": "A", "content": "x"}).json()
    d = client.patch(f"/api/dictionaries/{d['id']}", json={"content": "正 = 誤"}).json()
    assert d["rule_count"] == 1 and d["name"] == "A"
    assert client.post("/api/dictionaries", json={"name": " ", "content": ""}).status_code == 400
    assert client.delete(f"/api/dictionaries/{d['id']}").status_code == 200
    assert client.get(f"/api/dictionaries/{d['id']}").status_code == 404


def test_spa_fallback(client, data_dir):
    from app.config import settings

    settings.static_dir.mkdir(parents=True, exist_ok=True)
    (settings.static_dir / "index.html").write_text("<html>app</html>")
    assert "app" in client.get("/history").text
    assert client.get("/api/nope").status_code == 404


# ---- B1：手動新增的講者不能存成別人的聲紋 ----


def test_new_speaker_after_merge_cannot_save_voiceprint(client):
    job = upload(client)
    run_next_job(client)
    jid = job["id"]
    client.post(f"/api/jobs/{jid}/transcript/edit",
                json={"op": "merge_speakers", "source": "SPEAKER_01", "target": "SPEAKER_00"})
    r = client.post(f"/api/jobs/{jid}/transcript/edit",
                    json={"op": "set_speaker", "segment_id": 1, "speaker": None, "new_name": "Carol"})
    t = r.json()
    label = t["current"]["segments"][1]["speaker"]
    assert label.startswith("MANUAL_")
    assert t["voiceprint_speakers"] == ["SPEAKER_00"]  # 新講者與被合併掉的 SPEAKER_01 都不能存
    for lb in (label, "SPEAKER_01"):
        r = client.post(f"/api/jobs/{jid}/speakers/{lb}/voiceprint", json={"name": "Carol"})
        assert r.status_code == 400
    assert client.get("/api/voiceprints").json() == []


# ---- F17：已比對到聲紋的講者更新那筆聲紋，不新增重複的 ----


def test_update_matched_voiceprint_instead_of_creating_duplicate(client):
    from app import voiceprint

    vp = voiceprint.create("亞當", [0.1, 0.99, 0.0], "fake-embedding", "upload", 12.0)
    job = upload(client)
    run_next_job(client)
    jid = job["id"]
    t = client.get(f"/api/jobs/{jid}/transcript").json()
    assert t["current"]["speakers"]["SPEAKER_01"]["voiceprint_id"] == vp["id"]

    r = client.post(f"/api/jobs/{jid}/speakers/SPEAKER_01/voiceprint",
                    json={"name": "Alex", "voiceprint_id": vp["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["voiceprint_updated"] is True and r.json()["voiceprint"]["id"] == vp["id"]
    vps = voiceprint.list_all()
    assert len(vps) == 1 and vps[0]["name"] == "Alex" and vps[0]["embedding"] == [0.0, 1.0, 0.0]
    assert vps[0]["source"] == f"job:{jid}"

    # 要更新的聲紋已被刪除：改成新增一筆，並把講者連到新的那筆
    voiceprint.delete(vp["id"])
    r = client.post(f"/api/jobs/{jid}/speakers/SPEAKER_01/voiceprint",
                    json={"name": "Alex", "voiceprint_id": vp["id"]})
    assert r.status_code == 200 and r.json()["voiceprint_updated"] is False
    new_id = r.json()["voiceprint"]["id"]
    assert new_id != vp["id"] and r.json()["current"]["speakers"]["SPEAKER_01"]["voiceprint_id"] == new_id


# ---- B7：編輯在交易裡讀改寫，同時編輯不會互蓋 ----


def test_concurrent_edits_are_not_lost(client, monkeypatch):
    import threading
    import time as _time

    from app import transcript as tr
    from app.api import jobs as jobs_api

    job = upload(client, diarize="false")
    run_next_job(client)
    n = len(client.get(f"/api/jobs/{job['id']}/transcript").json()["current"]["segments"])
    real_apply = tr.apply_edit

    def slow_apply(t, op, reserved=None):
        _time.sleep(0.05)  # 拉大「讀了還沒寫」的時間窗
        return real_apply(t, op, reserved)

    monkeypatch.setattr(tr, "apply_edit", slow_apply)
    errors = []

    def edit(i):
        try:
            jobs_api.edit_transcript(job["id"], {"op": "set_text", "segment_id": i, "text": f"改{i}"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=edit, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert not errors
    segs = client.get(f"/api/jobs/{job['id']}/transcript").json()["current"]["segments"]
    assert [s["text"] for s in segs] == [f"改{i}" for i in range(n)]


def test_failed_edit_rolls_back(client):
    job = upload(client)
    run_next_job(client)
    r = client.post(f"/api/jobs/{job['id']}/transcript/edit", json={"op": "set_text", "segment_id": 99, "text": "x"})
    assert r.status_code == 400
    assert client.get(f"/api/jobs/{job['id']}/transcript").json()["edited"] is False


# ---- B9：上傳大小上限 ----


def test_upload_rejected_by_content_length_before_reading_body(client, monkeypatch):
    from app import main as main_mod
    from app.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    files = {"file": ("big.wav", b"\0" * (3 * 1024 * 1024), "audio/wav")}
    r = client.post("/api/jobs", files=files)
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    assert client.get("/api/jobs").json() == []
    # 上限之內照常上傳
    assert client.post("/api/jobs", files={"file": ("ok.wav", b"abc", "audio/wav")}).status_code == 200
    assert main_mod._upload_limit("GET", "/api/jobs") is None


def test_save_upload_enforces_limit_and_cleans_part_file(tmp_path):
    import io

    from app.api.jobs import UploadTooLarge, save_upload

    dest = tmp_path / "original.wav"
    assert save_upload(io.BytesIO(b"abcd"), dest, 10) == 4 and dest.read_bytes() == b"abcd"
    with pytest.raises(UploadTooLarge):
        save_upload(io.BytesIO(b"x" * 20), tmp_path / "big.wav", 10)
    assert not (tmp_path / "big.wav").exists() and not (tmp_path / "big.wav.part").exists()


# ---- B12：聲紋樣本 10–30 秒 ----


def test_voiceprint_sample_shorter_than_10s_rejected(client, monkeypatch):
    patch_audio(monkeypatch, seconds=8.0)
    r = client.post("/api/voiceprints", files={"file": ("a.m4a", b"abc", "audio/mp4")}, data={"name": "我"})
    assert r.status_code == 400
    assert "8.0 秒" in r.json()["detail"] and "至少要 10 秒" in r.json()["detail"]


def test_voiceprint_sample_longer_than_30s_uses_first_30s(client, monkeypatch):
    patch_audio(monkeypatch, seconds=45.0)
    seen = []
    backend = client.app.state.backend
    monkeypatch.setattr(backend, "embed", lambda audio: seen.append(len(audio)) or [0.0, 0.0, 1.0])
    r = client.post("/api/voiceprints", files={"file": ("a.m4a", b"abc", "audio/mp4")}, data={"name": "我"})
    assert r.status_code == 200 and r.json()["duration"] == 30.0
    assert seen == [30 * 16000]


# ---- B5：/healthz 回報 worker 執行緒 ----


def test_healthz_reports_worker_state(data_dir, monkeypatch):
    patch_audio(monkeypatch)
    app = create_app(backend=FakeBackend(), start_worker=True)
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"status": "ok", "worker": "alive"}
        worker = app.state.worker
        worker.stop()
        worker._thread.join(10)
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json()["worker"] == "dead"


def test_healthz_without_worker(client):
    assert client.get("/healthz").json() == {"status": "ok", "worker": "not_started"}


# ---- B11：API 端聲紋登記 OOM 不卸載 worker 的模型 ----


def test_enroll_oom_returns_503_without_unloading_worker_models(data_dir, monkeypatch):
    from app.pipeline import diarize as diarize_mod
    from app.pipeline.backend import GpuBackend
    from app.pipeline.models import ModelManager

    patch_audio(monkeypatch, seconds=12.0)
    manager = ModelManager(idle_seconds=600)
    with manager.use("whisper:large-v3", lambda: "W"):
        pass  # worker 已載入、目前閒置的模型
    calls = []
    monkeypatch.setattr(diarize_mod, "load_embedding_model", lambda: "E")

    def embed_audio(inf, audio):
        calls.append(1)
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(diarize_mod, "embed_audio", embed_audio)
    app = create_app(backend=GpuBackend(manager), start_worker=False)
    with TestClient(app) as c:
        r = c.post("/api/voiceprints", files={"file": ("a.m4a", b"abc", "audio/mp4")}, data={"name": "我"})
    assert r.status_code == 503 and r.json()["detail"] == "GPU 忙碌，請稍後重試"
    assert calls == [1]  # 不重試
    assert "whisper:large-v3" in manager.loaded()  # worker 的模型沒被卸掉
