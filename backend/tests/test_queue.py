import time

from app.jobs import queue


def new(name):
    return queue.create(filename=name, model="large-v3", diarize=True, num_speakers=None,
                        dictionary_ids=[1, 2], audio_ext=".wav", size_bytes=1)


def test_claim_in_order_and_requeue_unfinished(data_dir):
    a = new("a")
    time.sleep(0.01)
    b = new("b")
    assert queue.queue_position(b["id"]) == 1
    got = queue.claim_next()
    assert got["id"] == a["id"] and got["status"] == "running"
    assert queue.queue_position(b["id"]) == 1  # 前面有一個執行中
    # 模擬容器重啟
    assert queue.requeue_unfinished() == 1
    assert queue.get(a["id"])["status"] == "queued"
    assert queue.claim_next()["id"] == a["id"]


def test_rerun_goes_to_back_of_queue(data_dir):
    a = new("a")
    queue.update(a["id"], status="done")
    time.sleep(0.01)
    b = new("b")
    time.sleep(0.01)
    queue.requeue(a["id"], model="large-v3-turbo", diarize=False, num_speakers=2, dictionary_ids=[])
    assert queue.claim_next()["id"] == b["id"]
    nxt = queue.claim_next()
    assert nxt["id"] == a["id"] and nxt["model"] == "large-v3-turbo" and nxt["num_speakers"] == 2
    assert nxt["diarize"] is False


def test_cancel(data_dir):
    a = new("a")
    assert queue.request_cancel(a["id"]) == "canceled"
    b = new("b")
    queue.claim_next()
    assert queue.request_cancel(b["id"]) == "canceling"
    assert queue.is_cancel_requested(b["id"])
