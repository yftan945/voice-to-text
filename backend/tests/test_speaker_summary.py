"""講者分布摘要（jobs.speaker_summary）：計算、存檔／編輯／還原／刪除時更新、列表 API 帶出、舊任務補算。"""
import pytest
from fastapi.testclient import TestClient

from app import db, storage
from app import transcript as tr
from app.jobs import queue
from app.main import create_app

from fakes import FakeBackend, patch_audio


def sample():
    return {
        "duration": 60.0,
        "speakers": {
            "SPEAKER_01": {"name": "主持人", "voiceprint_id": 3, "similarity": 0.9},
            "SPEAKER_00": {"name": None, "voiceprint_id": None, "similarity": None},
        },
        "segments": [
            {"id": 0, "start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "一"},
            {"id": 1, "start": 4.5, "end": 10.0, "speaker": "SPEAKER_00", "text": "二"},  # 間隔 0.5 秒：併成一段
            {"id": 2, "start": 20.0, "end": 50.0, "speaker": "SPEAKER_01", "text": "三"},
            {"id": 3, "start": 50.0, "end": 52.0, "speaker": None, "text": "沒有講者"},
            {"id": 4, "start": 55.0, "end": 60.0, "speaker": "SPEAKER_00", "text": "四"},
        ],
    }


def test_summary_runs_and_shares():
    s = tr.speaker_summary(sample())
    assert s["duration"] == 60.0
    # 依代號排序（和結果頁的講者色順序一致），沒取名的顯示「講者 N」（N＝代號數字＋1）
    assert [(x["label"], x["name"]) for x in s["speakers"]] == [("SPEAKER_00", "講者 1"), ("SPEAKER_01", "主持人")]
    assert [x["seconds"] for x in s["speakers"]] == [14.5, 30.0]
    assert s["speakers"][0]["share"] == pytest.approx(14.5 / 44.5, abs=1e-4)
    assert sum(x["share"] for x in s["speakers"]) == pytest.approx(1.0, abs=1e-3)
    # 同講者間隔不到 1.5 秒合併；沒有講者的段落不畫
    assert s["runs"] == [[0.0, 10.0, 0], [20.0, 50.0, 1], [55.0, 60.0, 0]]


def test_manual_speakers_sort_after_recognized_ones():
    t = sample()
    t["speakers"]["MANUAL_00"] = {"name": "新講者", "voiceprint_id": None, "similarity": None}
    t["segments"][4]["speaker"] = "MANUAL_00"
    s = tr.speaker_summary(t)
    # 新增手動講者不會改變原本講者的索引（顏色）
    assert [x["label"] for x in s["speakers"]] == ["SPEAKER_00", "SPEAKER_01", "MANUAL_00"]
    assert s["runs"][-1] == [55.0, 60.0, 2]


def test_summary_without_speakers_or_segments():
    s = tr.speaker_summary({"speakers": {}, "segments": [{"id": 0, "start": 0, "end": 3, "speaker": None, "text": "x"}]})
    assert s["speakers"] == [] and s["runs"] == [] and s["duration"] == 3.0
    assert tr.speaker_summary({}) == {"duration": 0.0, "speakers": [], "runs": []}


def test_summary_caps_runs_for_long_recordings():
    # 兩人每 2 秒輪流講、共 1 小時：合併後仍有 1800 段，要壓成最多 SUMMARY_MAX_RUNS 段
    segs = [{"id": i, "start": i * 2.0, "end": i * 2.0 + 1.9, "speaker": f"SPEAKER_0{i % 2}", "text": "x"}
            for i in range(1800)]
    # 前半小時多半是 SPEAKER_00（每三段有兩段）
    for seg in segs[:900]:
        if seg["id"] % 3:
            seg["speaker"] = "SPEAKER_00"
    t = {"duration": 3600.0, "speakers": {"SPEAKER_00": {}, "SPEAKER_01": {}}, "segments": segs}
    s = tr.speaker_summary(t)
    assert 0 < len(s["runs"]) <= tr.SUMMARY_MAX_RUNS
    assert s["runs"][0][0] == 0.0 and s["runs"][-1][1] == pytest.approx(3600.0, abs=0.1)
    # 前半小時每一格都是講最多的 SPEAKER_00，合併成第一段
    assert s["runs"][0][2] == 0 and s["runs"][0][1] >= 1800
    # 相鄰的格子不會是同一位講者（已合併）
    assert all(a[2] != b[2] or a[1] != b[0] for a, b in zip(s["runs"], s["runs"][1:]))


def make_job():
    return queue.create(filename="a.m4a", model="large-v3", diarize=True, num_speakers=None,
                        dictionary_ids=[], audio_ext=".m4a", size_bytes=3)


def test_storage_keeps_summary_in_sync(data_dir):
    job = make_job()
    assert queue.get(job["id"])["speaker_summary"] is None

    storage.save_original(job["id"], sample())
    queue.update(job["id"], status="done", has_transcript=True)
    s = queue.get(job["id"])["speaker_summary"]
    assert [x["name"] for x in s["speakers"]] == ["講者 1", "主持人"]

    # 編輯（改名、改單段講者）後摘要跟著更新
    storage.edit(job["id"], lambda o, c, conn: tr.apply_edit(c, {"op": "rename_speaker", "speaker": "SPEAKER_00",
                                                                 "name": "Mark"}))
    assert queue.get(job["id"])["speaker_summary"]["speakers"][0]["name"] == "Mark"
    storage.edit(job["id"], lambda o, c, conn: tr.apply_edit(c, {"op": "merge_speakers", "source": "SPEAKER_00",
                                                                 "target": "SPEAKER_01"}))
    s = queue.get(job["id"])["speaker_summary"]
    assert [x["label"] for x in s["speakers"]] == ["SPEAKER_01"] and s["speakers"][0]["share"] == 1.0

    # 還原原始結果
    storage.save_edited(job["id"], None)
    s = queue.get(job["id"])["speaker_summary"]
    assert [x["name"] for x in s["speakers"]] == ["講者 1", "主持人"]

    # 只刪逐字稿：摘要一起清掉
    storage.delete_job(job["id"], "transcript")
    assert queue.get(job["id"])["speaker_summary"] is None


def test_save_edited_without_transcript_is_noop(data_dir):
    job = make_job()
    queue.update(job["id"], status="done")  # 排隊中不能編輯（見 test_review_fixes2）
    storage.save_edited(job["id"], None)
    assert queue.get(job["id"])["speaker_summary"] is None


def test_ensure_backfills_old_jobs_once(data_dir):
    job = make_job()
    storage.save_original(job["id"], sample())
    queue.update(job["id"], status="done", has_transcript=True)
    # 模擬欄位加上之前就完成的舊任務
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET speaker_summary = NULL WHERE id = ?", (job["id"],))
    jobs = queue.list_all()
    assert jobs[0]["speaker_summary"] is None
    storage.ensure_speaker_summaries(jobs)
    assert jobs[0]["speaker_summary"]["runs"] == [[0.0, 10.0, 0], [20.0, 50.0, 1], [55.0, 60.0, 0]]
    # 已寫回資料庫
    assert queue.get(job["id"])["speaker_summary"] == jobs[0]["speaker_summary"]


@pytest.fixture()
def client(data_dir, monkeypatch):
    patch_audio(monkeypatch)
    app = create_app(backend=FakeBackend(), start_worker=False)
    with TestClient(app) as c:
        yield c


def test_list_api_includes_summary(client):
    files = {"file": ("會議.qta", b"fake-audio-bytes", "audio/mp4")}
    job = client.post("/api/jobs", files=files, data={"model": "large-v3"}).json()
    assert job["speaker_summary"] is None
    claimed = queue.claim_next()
    client.app.state.worker.process(claimed)

    listed = {j["id"]: j for j in client.get("/api/jobs").json()}[job["id"]]
    s = listed["speaker_summary"]
    assert [x["label"] for x in s["speakers"]] == ["SPEAKER_00", "SPEAKER_01"]
    assert s["runs"] and all(len(r) == 3 for r in s["runs"])
    assert client.get(f"/api/jobs/{job['id']}").json()["speaker_summary"] == s

    # 改名後列表的名字跟著變
    r = client.post(f"/api/jobs/{job['id']}/transcript/edit",
                    json={"op": "rename_speaker", "speaker": "SPEAKER_00", "name": "Alex"})
    assert r.status_code == 200
    listed = {j["id"]: j for j in client.get("/api/jobs").json()}[job["id"]]
    assert listed["speaker_summary"]["speakers"][0]["name"] == "Alex"

    # 還原原始結果
    client.post(f"/api/jobs/{job['id']}/transcript/reset")
    listed = {j["id"]: j for j in client.get("/api/jobs").json()}[job["id"]]
    assert listed["speaker_summary"]["speakers"][0]["name"] == "講者 1"
