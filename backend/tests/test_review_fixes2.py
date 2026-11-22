"""第二輪審查修正：重新轉錄中禁止編輯（409）、列表補算講者摘要不因壞資料失敗、
沒取名的講者匯出成「講者 N」、時間碼關閉時中英之間補空格、/api/events 第一個事件是完整清單、gzip 壓縮。"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db, export, storage
from app import transcript as tr
from app.api import jobs as jobs_api
from app.jobs import queue
from app.main import create_app

from fakes import FakeBackend, patch_audio


@pytest.fixture()
def client(data_dir, monkeypatch):
    patch_audio(monkeypatch)
    app = create_app(backend=FakeBackend(), start_worker=False)
    with TestClient(app) as c:
        yield c


def upload(client):
    files = {"file": ("會議.qta", b"fake-audio-bytes", "audio/mp4")}
    r = client.post("/api/jobs", files=files, data={"model": "large-v3"})
    assert r.status_code == 200, r.text
    return r.json()


def run_next_job(client):
    job = queue.claim_next()
    assert job is not None
    client.app.state.worker.process(job)
    return queue.get(job["id"])


# ---- C5：排隊或重新轉錄中不能編輯 ----


def test_edits_rejected_while_rerun_is_queued_or_running(client):
    job = upload(client)
    run_next_job(client)
    jid = job["id"]
    r = client.post(f"/api/jobs/{jid}/transcript/edit", json={"op": "set_text", "segment_id": 0, "text": "先改一句"})
    assert r.status_code == 200

    # 重新轉錄：排隊中
    assert client.post(f"/api/jobs/{jid}/rerun", json={"model": "large-v3"}).status_code == 200
    assert queue.get(jid)["status"] == "queued"
    before = storage.load(jid)

    r = client.post(f"/api/jobs/{jid}/transcript/edit", json={"op": "set_text", "segment_id": 0, "text": "排隊中改"})
    assert r.status_code == 409 and r.json()["detail"] == "重新轉錄中，完成前不能編輯"
    r = client.post(f"/api/jobs/{jid}/transcript/reset")
    assert r.status_code == 409 and r.json()["detail"] == "重新轉錄中，完成前不能編輯"
    r = client.post(f"/api/jobs/{jid}/speakers/SPEAKER_00/voiceprint", json={"name": "Alex"})
    assert r.status_code == 409
    # 什麼都沒寫進去：逐字稿不變、聲紋庫沒有新增
    assert storage.load(jid) == before
    assert client.get("/api/voiceprints").json() == []

    # 處理中也一樣
    claimed = queue.claim_next()
    assert claimed["id"] == jid and queue.get(jid)["status"] == "running"
    r = client.post(f"/api/jobs/{jid}/transcript/edit", json={"op": "rename_speaker", "speaker": "SPEAKER_00",
                                                              "name": "X"})
    assert r.status_code == 409

    # 完成後可以再編輯
    client.app.state.worker.process(claimed)
    assert queue.get(jid)["status"] == "done"
    r = client.post(f"/api/jobs/{jid}/transcript/edit", json={"op": "set_text", "segment_id": 0, "text": "完成後改"})
    assert r.status_code == 200 and r.json()["current"]["segments"][0]["text"] == "完成後改"


def test_storage_edit_guard_in_same_transaction(data_dir):
    job = queue.create(filename="a.m4a", model="large-v3", diarize=True, num_speakers=None,
                       dictionary_ids=[], audio_ext=".m4a", size_bytes=3)
    storage.save_original(job["id"], {"speakers": {}, "segments": [{"id": 0, "start": 0, "end": 1, "speaker": None,
                                                                    "text": "一"}]})
    # 還在 queued（剛建立）：不能編輯
    with pytest.raises(storage.JobBusy):
        storage.edit(job["id"], lambda o, c, conn: tr.apply_edit(c, {"op": "set_text", "segment_id": 0, "text": "二"}))
    with pytest.raises(storage.JobBusy):
        storage.save_edited(job["id"], None)
    queue.update(job["id"], status="done", has_transcript=True)
    out = storage.edit(job["id"], lambda o, c, conn: tr.apply_edit(c, {"op": "set_text", "segment_id": 0,
                                                                        "text": "二"}))
    assert out is not None and out[1]["segments"][0]["text"] == "二"


# ---- c5：列表補算講者摘要 ----


def _done_job(name: str) -> dict:
    job = queue.create(filename=name, model="large-v3", diarize=True, num_speakers=None,
                       dictionary_ids=[], audio_ext=".m4a", size_bytes=3)
    queue.update(job["id"], status="done", has_transcript=True)
    return job


def _put_raw_transcript(job_id: str, original: str, edited: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO transcripts (job_id, original, edited, updated_at) VALUES (?, ?, ?, 0)",
                     (job_id, original, edited))
        conn.execute("UPDATE jobs SET speaker_summary = NULL WHERE id = ?", (job_id,))


def test_summary_backfill_skips_broken_rows(client, caplog):
    good = _done_job("好.m4a")
    null_segs = _done_job("segments 是 null.m4a")
    broken = _done_job("JSON 壞掉.m4a")
    _put_raw_transcript(good["id"], json.dumps({"speakers": {"SPEAKER_00": {}}, "segments": [
        {"id": 0, "start": 0, "end": 2, "speaker": "SPEAKER_00", "text": "一"}]}))
    _put_raw_transcript(null_segs["id"], json.dumps({"speakers": {}, "segments": None}))
    _put_raw_transcript(broken["id"], "{壞掉的 JSON")
    storage._summary_failed.clear()

    r = client.get("/api/jobs")
    assert r.status_code == 200
    listed = {j["id"]: j for j in r.json()}
    assert listed[good["id"]]["speaker_summary"]["speakers"][0]["name"] == "講者 1"
    assert listed[null_segs["id"]]["speaker_summary"] is None
    assert listed[broken["id"]]["speaker_summary"] is None
    assert "補算講者摘要失敗" in caplog.text
    # 好的那筆已寫回資料庫
    assert queue.get(good["id"])["speaker_summary"] is not None
    # 壞掉的不會每次列表都重試（不洗版）
    caplog.clear()
    assert client.get("/api/jobs").status_code == 200
    assert "補算講者摘要失敗" not in caplog.text


def test_summary_backfill_does_not_overwrite_newer_summary(data_dir, monkeypatch):
    job = _done_job("a.m4a")
    _put_raw_transcript(job["id"], json.dumps({"speakers": {"SPEAKER_00": {}}, "segments": [
        {"id": 0, "start": 0, "end": 2, "speaker": "SPEAKER_00", "text": "一"}]}))
    jobs = queue.list_all()
    newer = {"duration": 1.0, "speakers": [], "runs": []}
    real = tr.speaker_summary

    def slow_summary(t, duration=None):
        # 計算期間（交易外）另一個請求剛好寫了新的摘要
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET speaker_summary = ? WHERE id = ?", (json.dumps(newer), job["id"]))
        return real(t, duration)

    monkeypatch.setattr(tr, "speaker_summary", slow_summary)
    storage.ensure_speaker_summaries(jobs)
    assert queue.get(job["id"])["speaker_summary"] == newer


# ---- U2：沒取名的講者顯示成「講者 N」 ----


def _named_sample():
    return {
        "speakers": {"SPEAKER_00": {"name": "Alex"}, "SPEAKER_02": {"name": None}, "MANUAL_00": {"name": None}},
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "大家好"},
            {"id": 1, "start": 1.5, "end": 3.0, "speaker": "SPEAKER_02", "text": "我是第三位"},
            {"id": 2, "start": 3.5, "end": 4.0, "speaker": "MANUAL_00", "text": "手動"},
        ],
    }


def test_default_speaker_name():
    assert tr.default_speaker_name("SPEAKER_00") == "講者 1"
    assert tr.default_speaker_name("SPEAKER_11") == "講者 12"
    assert tr.default_speaker_name("MANUAL_00") == "MANUAL_00"
    t = _named_sample()
    assert tr.display_name(t, "SPEAKER_00") == "Alex"
    # 代號跳號（例如合併過）時用代號數字，不是排序位置
    assert tr.display_name(t, "SPEAKER_02") == "講者 3"
    assert tr.display_name(t, None) == ""


def test_exports_use_speaker_n():
    t = _named_sample()
    txt = export.to_txt(t, timestamps=True)
    assert "[00:00:01] 講者 3：我是第三位" in txt and "SPEAKER_" not in txt
    assert "講者 3：我是第三位" in export.to_txt(t, timestamps=False)
    md = export.to_markdown(t)
    assert "**講者 3** `[00:00:01]`" in md and "SPEAKER_" not in md
    srt = export.to_srt(t)
    assert "講者 3：我是第三位" in srt and "SPEAKER_" not in srt
    vtt = export.to_vtt(t)
    assert "<v 講者 3>我是第三位" in vtt and "SPEAKER_" not in vtt
    data = json.loads(export.to_json(t))
    # JSON 保留原始代號，名字欄位用「講者 N」
    assert data["segments"][1]["speaker"] == "SPEAKER_02" and data["segments"][1]["speaker_name"] == "講者 3"
    assert tr.speaker_summary(t)["speakers"][1]["name"] == "講者 3"


# ---- u2：時間碼關閉時中文接英文或數字補空格 ----


@pytest.mark.parametrize("prev,nxt,sep", [
    ("我們用", "Python 寫", " "),
    ("總共", "3 個人", " "),
    ("用 Python", "寫程式", " "),
    ("第 3", "個", " "),
    ("Hello.", "World", " "),
    ("你好", "今天", "，"),
    ("你好。", "今天", ""),
    ("你好，", "Python", ""),
    ("Hello.", "今天", ""),
])
def test_separator_spaces_between_cjk_and_latin(prev, nxt, sep):
    assert export._separator(prev, nxt) == sep


def test_txt_plain_mode_spaces_cjk_and_latin():
    t = {"speakers": {"SPEAKER_00": {"name": "Alex"}}, "segments": [
        {"id": 0, "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "我們用"},
        {"id": 1, "start": 1.2, "end": 2.0, "speaker": "SPEAKER_00", "text": "Python"},
        {"id": 2, "start": 2.2, "end": 3.0, "speaker": "SPEAKER_00", "text": "寫程式"},
    ]}
    assert export.to_txt(t, timestamps=False) == "Alex：我們用 Python 寫程式\n"


# ---- c9：/api/events 第一個事件是完整清單 ----


class _FakeRequest:
    """is_disconnected 第 n 次之後回 True，讓 SSE 產生器跑完一輪就結束。"""

    def __init__(self, app, rounds=1):
        self.app = app
        self.rounds = rounds

    async def is_disconnected(self):
        self.rounds -= 1
        return self.rounds < 0


def _collect(app) -> list[str]:
    async def run():
        resp = await jobs_api.all_events(_FakeRequest(app))
        return [c if isinstance(c, str) else c.decode() async for c in resp.body_iterator]

    return asyncio.run(run())


def test_events_first_message_is_full_list(client):
    # 沒有任何任務時也要送出（前端才會把舊清單清空）
    chunks = _collect(client.app)
    assert len(chunks) == 1 and chunks[0].startswith("event: jobs\n")
    data = json.loads(chunks[0].split("data: ", 1)[1])
    assert data == {"changed": [], "removed": [], "full": True}

    job = upload(client)
    data = json.loads(_collect(client.app)[0].split("data: ", 1)[1])
    assert data["full"] is True and [j["id"] for j in data["changed"]] == [job["id"]]


# ---- c7：gzip ----


def test_gzip_for_json_but_not_for_audio_range(client):
    for i in range(12):
        upload(client)
    r = client.get("/api/jobs", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    assert len(r.json()) == 12  # httpx 會自動解壓

    job = upload(client)
    run_next_job(client)
    path = storage.playback_path(job["id"])
    path.write_bytes(b"0123456789" * 400)
    r = client.get(f"/api/jobs/{job['id']}/audio", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
    assert r.status_code == 206
    assert "content-encoding" not in r.headers
    assert r.headers["content-range"] == "bytes 0-99/4000" and r.content == (b"0123456789" * 10)
    r = client.get(f"/api/jobs/{job['id']}/audio", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and "content-encoding" not in r.headers and len(r.content) == 4000


def test_gzip_skips_event_streams(client):
    job = upload(client)
    run_next_job(client)
    with client.stream("GET", f"/api/jobs/{job['id']}/events", headers={"Accept-Encoding": "gzip"}) as r:
        assert "content-encoding" not in r.headers
        body = "".join(r.iter_text())
    assert "event: job" in body

