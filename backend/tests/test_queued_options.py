"""修改排隊中任務的設定（PATCH /api/jobs/{id}/options）：各欄位、部分修改、狀態檢查、驗證、
YouTube 字幕選項、與 worker 領取的競態、排隊順序與批次不變、預估時間與 SSE 推送。"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.api import jobs as jobs_api
from app.jobs import queue
from app.main import create_app

from fakes import FakeBackend, patch_audio

VID = "abcDEF12_-x"


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


def patch(client, job_id, **body):
    return client.patch(f"/api/jobs/{job_id}/options", json=body)


def new_dict(client, name="d"):
    r = client.post("/api/dictionaries", json={"name": name, "content": "人工智慧 = 人工只能"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def youtube_job(*, manual_captions=("zh-TW",), use_captions=True, model="large-v3"):
    return queue.create(filename="影片", model=model, diarize=True, num_speakers=None, dictionary_ids=[],
                        audio_ext="", size_bytes=0, duration=120.0,
                        youtube={"video_id": VID, "manual_captions": list(manual_captions),
                                 "use_captions": use_captions})


OPTION_KEYS = ("model", "diarize", "num_speakers", "dictionary_ids", "use_captions")


def options(job_id):
    j = queue.get(job_id)
    return {k: j[k] for k in OPTION_KEYS}


def queued_ids():
    return [j["id"] for j in queue.list_active() if j["status"] == "queued"]


# ---- 成功修改 ----


def test_update_each_field(client):
    job = upload(client, model="large-v3", diarize="true")
    d1, d2 = new_dict(client, "d1"), new_dict(client, "d2")

    r = patch(client, job["id"], model="large-v3-turbo")
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "large-v3-turbo" and r.json()["id"] == job["id"]

    r = patch(client, job["id"], diarize=False)
    assert r.status_code == 200 and r.json()["diarize"] is False

    r = patch(client, job["id"], num_speakers=3)
    assert r.status_code == 200 and r.json()["num_speakers"] == 3

    r = patch(client, job["id"], dictionary_ids=[d2, d1])
    assert r.status_code == 200 and r.json()["dictionary_ids"] == [d2, d1]

    assert options(job["id"]) == {"model": "large-v3-turbo", "diarize": False, "num_speakers": 3,
                                  "dictionary_ids": [d2, d1], "use_captions": True}
    # 回傳格式同 GET /api/jobs/{id}（含排隊順位與預估欄位）
    got = client.get(f"/api/jobs/{job['id']}").json()
    assert r.json() == got
    assert got["status"] == "queued" and got["queue_rank"] == 1


def test_update_all_fields_at_once(client):
    job = upload(client, model="large-v3")
    d = new_dict(client)
    r = patch(client, job["id"], model="large-v3-turbo", diarize=False, num_speakers=2, dictionary_ids=[d])
    assert r.status_code == 200, r.text
    assert options(job["id"]) == {"model": "large-v3-turbo", "diarize": False, "num_speakers": 2,
                                  "dictionary_ids": [d], "use_captions": True}


def test_omitted_fields_unchanged(client):
    d = new_dict(client)
    job = upload(client, model="large-v3", diarize="true", num_speakers="4", dictionary_ids=str(d))
    before = options(job["id"])
    assert before["num_speakers"] == 4 and before["dictionary_ids"] == [d]

    r = patch(client, job["id"], diarize=False)
    assert r.status_code == 200
    assert options(job["id"]) == {**before, "diarize": False}

    # 空 body：什麼都不改，回傳目前的任務
    r = patch(client, job["id"])
    assert r.status_code == 200 and r.json()["model"] == "large-v3"
    assert options(job["id"]) == {**before, "diarize": False}

    # null 等於省略（num_speakers 除外）
    r = patch(client, job["id"], model=None, diarize=None, dictionary_ids=None)
    assert r.status_code == 200
    assert options(job["id"]) == {**before, "diarize": False}


@pytest.mark.parametrize("value", [None, 0, -1])
def test_num_speakers_null_or_zero_means_auto(client, value):
    job = upload(client, num_speakers="5")
    assert queue.get(job["id"])["num_speakers"] == 5
    r = patch(client, job["id"], num_speakers=value)
    assert r.status_code == 200 and r.json()["num_speakers"] is None


def test_empty_dictionary_list_clears(client):
    d = new_dict(client)
    job = upload(client, dictionary_ids=str(d))
    r = patch(client, job["id"], dictionary_ids=[])
    assert r.status_code == 200 and r.json()["dictionary_ids"] == []


def test_worker_uses_updated_options(client):
    job = upload(client, model="large-v3", diarize="true")
    assert patch(client, job["id"], model="large-v3-turbo", diarize=False).status_code == 200
    claimed = queue.claim_next()
    assert claimed["id"] == job["id"] and claimed["model"] == "large-v3-turbo" and claimed["diarize"] is False
    client.app.state.worker.process(claimed)
    done = queue.get(job["id"])
    assert done["status"] == "done", done["error"]
    assert done["model"] == "large-v3-turbo" and done["diarize"] is False


# ---- 狀態：不存在、已開始、已結束 ----


def test_not_found(client):
    r = patch(client, "nope", model="large-v3")
    assert r.status_code == 404 and r.json()["detail"] == "找不到這筆紀錄"


def test_running_job_conflict(client):
    job = upload(client, model="large-v3")
    queue.claim_next()
    before = options(job["id"])
    r = patch(client, job["id"], model="large-v3-turbo")
    assert r.status_code == 409 and r.json()["detail"] == "任務已經開始處理，無法修改設定"
    assert options(job["id"]) == before


@pytest.mark.parametrize("status", ["done", "failed", "canceled"])
def test_finished_job_conflict(client, status):
    job = upload(client, model="large-v3")
    queue.update(job["id"], status=status)
    before = options(job["id"])
    r = patch(client, job["id"], model="large-v3-turbo", diarize=False)
    assert r.status_code == 409 and r.json()["detail"] == "任務已經結束，無法修改設定"
    assert options(job["id"]) == before


def test_canceled_via_api_conflict(client):
    job = upload(client)
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "canceled"
    assert patch(client, job["id"], diarize=False).status_code == 409


def test_status_check_comes_before_validation(client):
    """已經開始處理時，即使參數不合法也回 409（先告訴使用者不能改）。"""
    job = upload(client)
    queue.claim_next()
    assert patch(client, job["id"], model="no-such-model").status_code == 409


# ---- 驗證 ----


@pytest.mark.parametrize("body, msg", [
    ({"model": "no-such-model"}, "不支援的模型"),
    ({"model": "breeze-asr-25"}, "尚未安裝"),  # conftest 模擬 Breeze 尚未轉檔
    ({"num_speakers": 21}, "講者人數要介於 1 到 20"),
    ({"dictionary_ids": [9999]}, "字典不存在"),
])
def test_invalid_options_rejected(client, body, msg):
    job = upload(client, model="large-v3", num_speakers="2")
    before = options(job["id"])
    r = patch(client, job["id"], **body)
    assert r.status_code == 400 and msg in r.json()["detail"]
    assert options(job["id"]) == before


def test_deleted_stored_dictionary_is_dropped(client):
    """任務原本存的字典後來被刪除：只改模型也能存，該 id 被剔除；送回原本的 id 也一樣剔除。"""
    d1, d2 = new_dict(client, "d1"), new_dict(client, "d2")
    job = upload(client, model="large-v3", dictionary_ids=f"{d1},{d2}")
    assert client.delete(f"/api/dictionaries/{d1}").status_code == 200

    r = patch(client, job["id"], model="large-v3-turbo")
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "large-v3-turbo" and r.json()["dictionary_ids"] == [d2]
    assert options(job["id"])["dictionary_ids"] == [d2]

    # 前端還帶著已刪除的舊 id（原本就存著的）送回來：剔除而不是 400
    job2 = upload(client, dictionary_ids=f"{d2}")
    assert client.delete(f"/api/dictionaries/{d2}").status_code == 200
    r = patch(client, job2["id"], dictionary_ids=[d2], diarize=False)
    assert r.status_code == 200, r.text
    assert r.json()["dictionary_ids"] == [] and r.json()["diarize"] is False


def test_new_missing_dictionary_still_rejected(client):
    """這次新加入的字典不存在仍回 400，資料不變。"""
    d = new_dict(client)
    job = upload(client, dictionary_ids=str(d))
    before = options(job["id"])
    r = patch(client, job["id"], dictionary_ids=[d, 9999])
    assert r.status_code == 400 and "字典不存在" in r.json()["detail"]
    assert options(job["id"]) == before


def test_wrong_types_rejected(client):
    job = upload(client)
    assert patch(client, job["id"], num_speakers="多").status_code == 422
    assert patch(client, job["id"], dictionary_ids="1,2").status_code == 422


def test_use_captions_ignored_for_file_jobs(client):
    """非 YouTube 來源：use_captions 沒有意義，傳了也忽略（同重新轉錄），其他欄位照常修改。"""
    job = upload(client, model="large-v3")
    r = patch(client, job["id"], use_captions=False, diarize=False)
    assert r.status_code == 200
    assert r.json()["use_captions"] is True and r.json()["diarize"] is False
    assert queue.get(job["id"])["use_captions"] is True


# ---- YouTube 來源 ----


def test_youtube_toggle_use_captions(client):
    job = youtube_job(manual_captions=("zh-TW",))
    r = patch(client, job["id"], use_captions=False)
    assert r.status_code == 200 and r.json()["use_captions"] is False
    r = patch(client, job["id"], use_captions=True)
    assert r.status_code == 200 and r.json()["use_captions"] is True
    assert r.json()["caption_lang"] == "zh-TW"


def test_youtube_use_captions_requires_manual_captions(client):
    job = youtube_job(manual_captions=(), use_captions=False)
    r = patch(client, job["id"], use_captions=True)
    assert r.status_code == 400 and "人工字幕" in r.json()["detail"]
    assert queue.get(job["id"])["use_captions"] is False
    # 改成用模型轉錄一定可以
    assert patch(client, job["id"], use_captions=False, model="large-v3-turbo").status_code == 200


def test_youtube_caption_route_allows_uninstalled_model(client):
    """走字幕路線不需要 Whisper：模型還沒安裝也可以（同建立 YouTube 任務）；改成用模型轉錄時才檢查模型。"""
    job = youtube_job(manual_captions=("zh-TW",), use_captions=True)
    r = patch(client, job["id"], model="breeze-asr-25")
    assert r.status_code == 200 and r.json()["model"] == "breeze-asr-25"
    # 只改講者辨識也不會被尚未安裝的模型擋下
    assert patch(client, job["id"], diarize=False).status_code == 200

    r = patch(client, job["id"], use_captions=False)
    assert r.status_code == 400 and "尚未安裝" in r.json()["detail"]
    assert queue.get(job["id"])["use_captions"] is True
    # 同時換成已安裝的模型就可以
    r = patch(client, job["id"], use_captions=False, model="large-v3")
    assert r.status_code == 200 and r.json()["use_captions"] is False and r.json()["model"] == "large-v3"

    # 字幕路線仍然檢查模型是否存在、人數範圍
    assert patch(client, job["id"], use_captions=True, model="no-such-model").status_code == 400
    assert patch(client, job["id"], num_speakers=21).status_code == 400


def test_youtube_without_captions_uses_full_validation(client):
    """use_captions=True 但影片沒有人工字幕：實際會用模型轉錄，模型要已安裝。"""
    job = youtube_job(manual_captions=(), use_captions=True)
    r = patch(client, job["id"], model="breeze-asr-25")
    assert r.status_code == 400 and "尚未安裝" in r.json()["detail"]


# ---- 競態：worker 同時領取 ----


def test_claimed_during_validation_returns_409_and_keeps_data(client, monkeypatch):
    """API 檢查時還在排隊，寫入前被 worker 領走：回 409，資料不被改。"""
    job = upload(client, model="large-v3", diarize="true")
    real = jobs_api._validate_options

    def validate_then_worker_claims(*a, **kw):
        real(*a, **kw)
        assert queue.claim_next()["id"] == job["id"]

    monkeypatch.setattr(jobs_api, "_validate_options", validate_then_worker_claims)
    r = patch(client, job["id"], model="large-v3-turbo", diarize=False, num_speakers=2)
    assert r.status_code == 409 and r.json()["detail"] == "任務已經開始處理，無法修改設定"
    got = queue.get(job["id"])
    assert got["status"] == "running"
    assert got["model"] == "large-v3" and got["diarize"] is True and got["num_speakers"] is None


def test_update_queued_options_conditional(data_dir):
    job = queue.create(filename="a", model="large-v3", diarize=True, num_speakers=None, dictionary_ids=[1],
                       audio_ext=".wav", size_bytes=1)
    got = queue.update_queued_options(job["id"], model="large-v3-turbo", diarize=False, dictionary_ids=[2, 3])
    assert got["model"] == "large-v3-turbo" and got["diarize"] is False and got["dictionary_ids"] == [2, 3]
    # 沒有欄位：只確認仍在排隊中
    assert queue.update_queued_options(job["id"])["model"] == "large-v3-turbo"

    queue.claim_next()
    with pytest.raises(queue.NotQueuedError, match="開始處理"):
        queue.update_queued_options(job["id"], model="large-v3")
    with pytest.raises(queue.NotQueuedError, match="開始處理"):
        queue.update_queued_options(job["id"])
    assert queue.get(job["id"])["model"] == "large-v3-turbo"

    queue.update(job["id"], status="done")
    with pytest.raises(queue.NotQueuedError, match="結束"):
        queue.update_queued_options(job["id"], model="large-v3")
    with pytest.raises(KeyError):
        queue.update_queued_options("nope", model="large-v3")
    # 只能改設定欄位
    with pytest.raises(ValueError):
        queue.update_queued_options(job["id"], position=0)
    with pytest.raises(ValueError):
        queue.update_queued_options(job["id"], status="queued")


def test_concurrent_claim_and_update(data_dir):
    """worker 領取與修改設定同時發生：修改成功 ⇔ worker 領到的是新設定；失敗時資料不變。"""
    for _ in range(30):
        job = queue.create(filename="a", model="large-v3", diarize=True, num_speakers=None, dictionary_ids=[],
                           audio_ext=".wav", size_bytes=1)
        barrier = threading.Barrier(2)
        out: dict[str, object] = {}

        def claim():
            barrier.wait()
            out["claimed"] = queue.claim_next()

        def update():
            barrier.wait()
            try:
                queue.update_queued_options(job["id"], model="large-v3-turbo")
                out["updated"] = True
            except queue.NotQueuedError:
                out["updated"] = False

        threads = [threading.Thread(target=claim), threading.Thread(target=update)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        claimed = out["claimed"]
        assert claimed["id"] == job["id"]
        expected = "large-v3-turbo" if out["updated"] else "large-v3"
        assert claimed["model"] == expected
        assert queue.get(job["id"])["model"] == expected
        queue.update(job["id"], status="done")


# ---- 排隊順序與批次 ----


def test_order_position_and_batch_unchanged(client):
    a = upload(client, "a.m4a", model="large-v3", batch_id="b1")
    b = upload(client, "b.m4a", model="large-v3", batch_id="b1")
    c = upload(client, "c.m4a", model="large-v3", batch_id="b1")
    queue.move(c["id"], to="front")
    order = queued_ids()
    before = {x: queue.get(x) for x in order}

    r = patch(client, b["id"], model="large-v3-turbo", diarize=False, num_speakers=2)
    assert r.status_code == 200
    assert queued_ids() == order == [c["id"], a["id"], b["id"]]
    after_b = queue.get(b["id"])
    for k in ("position", "queued_at", "batch_id", "created_at", "status", "stage"):
        assert after_b[k] == before[b["id"]][k], k
    assert after_b["batch_id"] == "b1"
    # 同批其他任務完全不變
    for x in (a["id"], c["id"]):
        assert queue.get(x) == before[x]
    assert [j["id"] for j in queue.list_batch("b1")] == [a["id"], b["id"], c["id"]]
    # 領取順序照舊
    assert [queue.claim_next()["id"] for _ in range(3)] == [c["id"], a["id"], b["id"]]


# ---- 預估時間與推送 ----


def test_estimate_follows_new_model(client):
    job = upload(client, model="large-v3", diarize="false")
    queue.update(job["id"], duration=3000.0)
    before = client.get("/api/jobs").json()[0]
    r = patch(client, job["id"], model="large-v3-turbo")
    assert r.status_code == 200
    after = client.get("/api/jobs").json()[0]
    # large-v3 約 180 秒／3016 秒音檔、turbo 約 68 秒：換成 turbo 後預估明顯變短
    assert after["estimate_seconds"] < before["estimate_seconds"]
    assert r.json()["estimate_seconds"] == after["estimate_seconds"]


def test_estimate_of_later_jobs_updates(client):
    """前面的任務換模型，後面任務的預估開始時間也跟著變。"""
    a = upload(client, "a.m4a", model="large-v3", diarize="false")
    b = upload(client, "b.m4a", model="large-v3", diarize="false")
    queue.update(a["id"], duration=3000.0)
    queue.update(b["id"], duration=60.0)
    before = client.get(f"/api/jobs/{b['id']}").json()["eta_start_seconds"]
    assert patch(client, a["id"], model="large-v3-turbo").status_code == 200
    assert client.get(f"/api/jobs/{b['id']}").json()["eta_start_seconds"] < before


def test_sse_signature_changes_with_options(client):
    """SSE 只推送 signature 有變的任務：只改字典或人數（預估不變）也要推送。"""
    d = new_dict(client)
    job = upload(client)
    sig = jobs_api._signature(client.get(f"/api/jobs/{job['id']}").json())
    for body in ({"dictionary_ids": [d]}, {"num_speakers": 2}, {"diarize": False}, {"model": "large-v3-turbo"}):
        assert patch(client, job["id"], **body).status_code == 200
        new_sig = jobs_api._signature(client.get(f"/api/jobs/{job['id']}").json())
        assert new_sig != sig, body
        sig = new_sig
    yt = youtube_job()
    sig = jobs_api._signature(client.get(f"/api/jobs/{yt['id']}").json())
    assert patch(client, yt["id"], use_captions=False).status_code == 200
    assert jobs_api._signature(client.get(f"/api/jobs/{yt['id']}").json()) != sig
