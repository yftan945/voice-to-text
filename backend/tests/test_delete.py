import pytest

from app import storage
from app.jobs import queue


def make_job(status="done"):
    job = queue.create(filename="a.m4a", model="large-v3", diarize=True, num_speakers=None,
                       dictionary_ids=[], audio_ext=".m4a", size_bytes=3)
    d = storage.job_dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    storage.original_path(job).write_bytes(b"abc")
    storage.playback_path(job["id"]).write_bytes(b"m4a")
    storage.save_original(job["id"], {"segments": [], "speakers": {}})
    queue.update(job["id"], status=status, has_transcript=True, has_playback=True)
    return queue.get(job["id"])


def test_delete_transcript_keeps_audio(data_dir):
    job = make_job()
    out = storage.delete_job(job["id"], "transcript")
    assert out["has_transcript"] is False and out["has_audio"] is True
    assert storage.load(job["id"]) is None
    assert storage.original_path(job).exists()
    assert storage.playback_path(job["id"]).exists()


def test_delete_audio_keeps_transcript(data_dir):
    job = make_job()
    out = storage.delete_job(job["id"], "audio")
    assert out["has_audio"] is False and out["has_playback"] is False
    assert out["has_transcript"] is True
    assert storage.load(job["id"]) is not None
    assert not storage.job_dir(job["id"]).exists()


def test_delete_all(data_dir):
    job = make_job()
    assert storage.delete_job(job["id"], "all") is None
    assert queue.get(job["id"]) is None
    assert storage.load(job["id"]) is None
    assert not storage.job_dir(job["id"]).exists()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_delete_refused_while_active(data_dir, status):
    job = make_job(status=status)
    for mode in storage.DELETE_MODES:
        with pytest.raises(storage.JobBusy):
            storage.delete_job(job["id"], mode)
    assert storage.original_path(job).exists()


def test_delete_bad_mode_and_missing(data_dir):
    job = make_job()
    with pytest.raises(ValueError):
        storage.delete_job(job["id"], "everything")
    with pytest.raises(KeyError):
        storage.delete_job("nope", "all")


def test_delete_all_removes_rows_in_one_transaction(data_dir):
    from app import db

    job = make_job()
    storage.delete_job(job["id"], "all")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transcripts WHERE job_id = ?", (job["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0] == 0


def test_delete_refused_when_job_starts_running_keeps_transcript(data_dir):
    """刪除用條件式 UPDATE：任務已經被領走（running）時不會刪到逐字稿或改到欄位。"""
    job = make_job()
    queue.update(job["id"], status="running")
    with pytest.raises(storage.JobBusy):
        storage.delete_job(job["id"], "transcript")
    got = queue.get(job["id"])
    assert got["has_transcript"] is True and storage.load(job["id"]) is not None
