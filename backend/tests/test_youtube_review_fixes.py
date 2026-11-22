"""code review 修正：字幕下載失敗改用模型轉錄、resolve 不占預設 executor、字幕去重、資訊快取沿用、
錯誤分類、下載參數、模型沒安裝時的字幕路線、下載計時、yt-dlp 版本重試。"""
from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time

import pytest

from app import storage, youtube_cache
from app import youtube as yt
from app.jobs import queue
from app.pipeline import captions, youtube_source, ytdlp

# 共用 test_youtube 的 mock 與 fixture（client、backend）
from test_youtube import VID, VID2, VTT_ZH, FakeYtdlp, backend, client, create, make_info, run_next  # noqa: F401

@pytest.fixture(autouse=True)
def _clear_info_cache():
    """影片資訊快取是行程內的全域狀態，每個測試前後清掉。"""
    youtube_cache.clear()
    yield
    youtube_cache.clear()


RATE_LIMITED = yt.classify_error("ERROR: Unable to download video subtitles for 'zh-TW': HTTP Error 429: "
                                 "Too Many Requests")


# ---- 1. 字幕下載失敗不能讓任務失敗 ----


def test_caption_download_failure_falls_back_to_model(client, monkeypatch, backend):
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])}, caption_error=RATE_LIMITED)
    job = create(client)
    done = run_next(client)
    assert done["status"] == "done", done["error"]
    assert done["caption_source"] == "none" and done["audio_ext"] == ".webm"
    # 字幕失敗後音訊照常單獨下載
    assert fake.downloads == [{"video_id": VID, "audio": False, "caption_lang": "zh-TW"},
                              {"video_id": VID, "audio": True, "caption_lang": None}]
    assert "transcribe" in [c[0] for c in backend.calls]
    warn = done["warnings"][0]
    assert "下載失敗" in warn and "429" in warn and "已改用模型轉錄" in warn and "台灣繁體中文" in warn
    t = client.get(f"/api/jobs/{job['id']}/transcript").json()["current"]
    assert t["text_source"]["type"] == "model" and warn in t["warnings"]
    assert not list(storage.job_dir(job["id"]).glob("captions.*"))


def test_caption_only_route_failure_falls_back_to_model(client, monkeypatch, backend):
    """重新轉錄只缺字幕（音訊已在）時字幕失敗，也改用模型轉錄。"""
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])})
    job = create(client, use_captions=False)
    assert run_next(client)["caption_source"] == "none"
    assert fake.downloads == [{"video_id": VID, "audio": True, "caption_lang": None}]

    fake.caption_error = RATE_LIMITED
    assert client.post(f"/api/jobs/{job['id']}/rerun", json={"use_captions": True}).status_code == 200
    done = run_next(client)
    assert done["status"] == "done", done["error"]
    assert done["caption_source"] == "none" and any("已改用模型轉錄" in w for w in done["warnings"])
    assert fake.downloads[1:] == [{"video_id": VID, "audio": False, "caption_lang": "zh-TW"}]

    # 字幕恢復後重新轉錄：只下載字幕，不再下載音訊
    fake.caption_error = None
    assert client.post(f"/api/jobs/{job['id']}/rerun", json={"use_captions": True}).status_code == 200
    done = run_next(client)
    assert done["caption_source"] == "manual:zh-TW" and not done["warnings"]
    assert fake.downloads[2:] == [{"video_id": VID, "audio": False, "caption_lang": "zh-TW"}]


def test_caption_timeout_also_falls_back(client, monkeypatch):
    FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])},
              caption_error=yt.YouTubeError("timeout", "連線 YouTube 逾時，請稍後再試。", 502))
    create(client)
    done = run_next(client)
    assert done["status"] == "done" and "逾時" in done["warnings"][0]


def test_audio_failure_still_fails_job(client, monkeypatch):
    FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])},
              download_error=yt.classify_error("ERROR: [youtube] x: This video has been removed by the uploader"))
    create(client)
    done = run_next(client)
    assert done["status"] == "failed" and "下架" in done["error"]


def _prepare_job(**kw):
    return queue.create(filename="t", model="large-v3", diarize=False, num_speakers=None, dictionary_ids=[],
                        audio_ext="", size_bytes=0, duration=4.0,
                        youtube={"video_id": VID, "manual_captions": ["zh-TW"], "use_captions": True, **kw})


@pytest.mark.parametrize("caption_error", [None, RATE_LIMITED])
def test_download_progress_never_goes_backwards(data_dir, monkeypatch, caption_error):
    """字幕與音訊分開下載，進度條不會從 100% 跳回 5%。"""
    FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])}, caption_error=caption_error)
    seen: list[tuple[str, float]] = []
    prep = youtube_source.prepare(_prepare_job(), lambda label, p: seen.append((label, p)), lambda: None,
                                  max_bytes=10**9)
    values = [p for _, p in seen]
    assert values == sorted(values) and values[-1] == 1.0
    labels = [label for label, _ in seen]
    assert labels.index("下載字幕中") < labels.index("下載音訊中")
    # 字幕只占前面一小段
    assert max(p for label, p in seen if label == "下載字幕中") <= 0.05
    assert (prep.caption_lang == "zh-TW") is (caption_error is None)


# ---- 2. /api/youtube/resolve 不占預設 executor ----


def test_resolve_50_lines_does_not_block_default_executor(client, monkeypatch):
    release = threading.Event()
    started: list[str] = []
    ids = [f"v{i:010d}" for i in range(50)]

    def slow_fetch(video_id, **kw):
        started.append(video_id)
        release.wait(20)
        return make_info(video_id)

    FakeYtdlp(monkeypatch, {})
    monkeypatch.setattr(ytdlp, "fetch_video_info", slow_fetch)
    result: dict = {}

    def post():
        result["r"] = client.post("/api/youtube/resolve",
                                  json={"text": "\n".join(f"https://youtu.be/{v}" for v in ids)})

    t = threading.Thread(target=post)
    t.start()
    try:
        deadline = time.monotonic() + 10
        while len(started) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(started) == 3  # 同時最多 3 個 yt-dlp
        # 50 個網址在排隊時，其他 asyncio.to_thread 呼叫（例如上傳存檔）照常馬上完成
        t0 = time.monotonic()
        got = client.portal.call(asyncio.wait_for, asyncio.to_thread(lambda: "ok"), 5)
        assert got == "ok" and time.monotonic() - t0 < 2
        # 一般 API 也照常回應
        assert client.get("/api/jobs").status_code == 200
    finally:
        release.set()
        t.join(30)
    r = result["r"]
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 50 and body["truncated"] is False
    assert all(i["video"]["error"] is None for i in body["items"])


def test_resolve_truncates_after_limit(client, monkeypatch):
    FakeYtdlp(monkeypatch, {})
    text = "\n".join(f"not-a-url-{i}" for i in range(60))
    body = client.post("/api/youtube/resolve", json={"text": text}).json()
    assert len(body["items"]) == 50 and body["truncated"] is True


# ---- 7. split_lines ----


def test_collect_lines_dedupes_and_stops_early():
    assert yt.collect_lines("a b a,c，b\n\nd", 10) == (["a", "b", "c", "d"], False)
    assert yt.collect_lines("a b c", 3) == (["a", "b", "c"], False)
    assert yt.collect_lines("a b c d", 3) == (["a", "b", "c"], True)
    assert yt.collect_lines("a a a a b", 1) == (["a"], True)
    assert yt.split_lines("x y x", 50) == ["x", "y"]
    assert yt.collect_lines("", 5) == ([], False)


# ---- 3. 字幕去重不能誤刪合法的重複句 ----


def test_separate_identical_cues_are_kept():
    text = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n對\n\n"
            "00:00:02.000 --> 00:00:04.000\n你覺得呢\n\n"
            "00:00:05.000 --> 00:00:06.000\n對\n")
    segs = captions.parse(text, "vtt")
    assert [(s["start"], s["end"], s["text"]) for s in segs] == [
        (1.0, 2.0, "對"), (2.0, 4.0, "你覺得呢"), (5.0, 6.0, "對")]


def test_identical_cues_with_gap_are_two_segments():
    """01→02「對」與 05→06「對」中間沒有別的字幕：仍然是兩段（不合併、不去重）。"""
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n對\n\n00:00:05.000 --> 00:00:06.000\n對\n"
    segs = captions.parse(text, "vtt")
    assert [(s["start"], s["end"], s["text"]) for s in segs] == [(1.0, 2.0, "對"), (5.0, 6.0, "對")]
    srt = "1\n00:00:01,000 --> 00:00:02,000\n對\n\n2\n00:00:05,000 --> 00:00:06,000\n對\n"
    assert [s["text"] for s in captions.parse(srt, "srt")] == ["對", "對"]


def test_repeated_line_after_gap_is_not_treated_as_rolling():
    """不相接的 cue 開頭跟上一個 cue 結尾同一行：不是捲動式字幕，整個 cue 保留。"""
    text = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n好\n\n"
            "00:00:04.000 --> 00:00:05.000\n好\n我們開始\n")
    segs = captions.parse(text, "vtt")
    assert [s["text"] for s in segs] == ["好", "好，我們開始"]


def test_rolling_captions_are_still_deduped():
    text = ("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n第一行\n\n"
            "00:00:02.000 --> 00:00:04.000\n第一行\n第二行\n\n"
            "00:00:04.030 --> 00:00:06.000\n第二行\n第三行\n\n"
            "00:00:06.000 --> 00:00:06.010\n第三行\n\n"
            "00:00:06.010 --> 00:00:07.000\n第三行\n")
    segs = captions.parse(text, "vtt")
    assert [(s["start"], s["end"], s["text"]) for s in segs] == [
        (0.0, 2.0, "第一行"), (2.0, 4.0, "第二行"), (4.03, 7.0, "第三行")]


def test_contiguous_identical_cues_merge():
    text = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n一樣的話\n\n"
            "00:00:02.000 --> 00:00:03.000\n一樣的話\n")
    assert [(s["start"], s["end"]) for s in captions.parse(text, "vtt")] == [(1.0, 3.0)]


# ---- 11. SRT 空白行、行內時間的逐字路線 ----


def test_srt_cue_with_blank_line_keeps_all_text():
    srt = ("1\n00:00:01,000 --> 00:00:03,000\n前半段\n\n後半段\n\n"
           "2\n00:00:03,000 --> 00:00:04,000\n下一句\n\n"
           "3\n00:00:05,000 --> 00:00:06,000\n2024\n\n")
    segs = captions.parse(srt, "srt")
    assert [(s["start"], s["end"], s["text"]) for s in segs] == [
        (1.0, 3.0, "前半段，後半段"), (3.0, 4.0, "下一句"), (5.0, 6.0, "2024")]


def test_srt_without_index_lines():
    srt = "00:00:01,000 --> 00:00:02,000\n甲\n\n00:00:02,500 --> 00:00:03,000\n乙\n"
    assert [s["text"] for s in captions.parse(srt, "srt")] == ["甲", "乙"]


def test_inline_word_cue_uses_finish_text():
    """有行內時間的 cue 走逐字路線時，中文之間的空白一樣換成「，」。"""
    text = ("WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n"
            "大家好<00:00:02.000><c> 我是</c><00:00:03.000><c>亞當</c><00:00:04.000><c> 謝謝 大家</c>\n")
    (seg,) = captions.parse(text, "vtt")
    assert seg["text"] == "大家好，我是亞當，謝謝，大家"
    assert "".join(w["text"] for w in seg["words"]) == seg["text"]
    out = captions.assign_caption_speakers([seg], [(0.0, 10.0, "A")])
    assert out[0]["text"] == "大家好，我是亞當，謝謝，大家"


# ---- 5／13. 影片資訊快取：worker 沿用、過期重新解析、錯誤也快取 ----


def test_worker_reuses_fresh_info(client, monkeypatch):
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])})
    create(client)
    assert fake.fetches == [VID]
    assert run_next(client)["status"] == "done"
    assert fake.fetches == [VID]  # 播放清單／貼上時查過一次，worker 不再解析


def test_worker_refetches_when_info_is_old(client, monkeypatch):
    fake = FakeYtdlp(monkeypatch, {VID: make_info()})
    create(client)
    old = time.time() - youtube_cache.REUSE_SECONDS - 60
    youtube_cache.put(VID, make_info(), yt.video_summary(make_info()), now=old)
    assert run_next(client)["status"] == "done"
    assert fake.fetches == [VID, VID]


def test_worker_refetches_when_cached_format_url_expired(client, monkeypatch):
    fake = FakeYtdlp(monkeypatch, {VID: make_info()})
    create(client)
    real = fake.download
    calls = []

    def flaky(info, dest_dir, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise yt.classify_error("ERROR: unable to download video data: HTTP Error 404: Not Found")
        return real(info, dest_dir, **kw)

    monkeypatch.setattr(ytdlp, "download", flaky)
    done = run_next(client)
    assert done["status"] == "done", done["error"]
    assert fake.fetches == [VID, VID] and len(calls) == 2


def test_cache_stores_full_info_without_heavy_fields():
    youtube_cache.clear()
    info = {**make_info(subs=["zh-TW"], auto=["en"]), "thumbnails": [{"url": "x"}], "heatmap": [1],
            "formats": [{"format_id": "251", "ext": "webm"}, {"format_id": "sb0", "ext": "mhtml",
                                                                "format_note": "storyboard"}]}
    youtube_cache.put(VID, info, yt.video_summary(info))
    got, fetched_at = youtube_cache.info(VID)
    assert "automatic_captions" not in got and "thumbnails" not in got and "heatmap" not in got
    assert got["subtitles"] == info["subtitles"] and [f["format_id"] for f in got["formats"]] == ["251"]
    assert time.time() - fetched_at < 5
    assert youtube_cache.summary(VID)["manual_captions"] == ["zh-TW"]
    assert youtube_cache.info(VID, max_age=0) is None
    youtube_cache.clear()


def test_cache_is_bounded(monkeypatch):
    youtube_cache.clear()
    monkeypatch.setattr(youtube_cache, "MAX_ENTRIES", 3)
    now = time.time()
    for i in range(5):
        vid = f"v{i:010d}"
        youtube_cache.put(vid, make_info(vid), yt.video_summary(make_info(vid)), now=now + i)
    assert youtube_cache.info("v0000000000") is None and youtube_cache.info("v0000000004") is not None
    youtube_cache.clear()


def test_errors_are_cached_for_five_minutes(client, monkeypatch):
    fake = FakeYtdlp(monkeypatch, {VID: yt.classify_error("ERROR: [youtube] x: Private video. Sign in")})
    for _ in range(3):
        r = client.get(f"/api/youtube/videos/{VID}")
        assert r.status_code == 422 and "私人影片" in r.json()["detail"]
    assert fake.fetches == [VID]
    # 5 分鐘後重新查
    youtube_cache.put_error(VID, yt.classify_error("ERROR: Private video"),
                            now=time.time() - youtube_cache.ERROR_SECONDS - 1)
    client.get(f"/api/youtube/videos/{VID}")
    assert fake.fetches == [VID, VID]


# ---- 9. 錯誤分類 ----


@pytest.mark.parametrize("stderr, code", [
    ("ERROR: unable to download video data: HTTP Error 404: Not Found", "expired"),
    ("ERROR: unable to download video data: HTTP Error 410: Gone", "expired"),
    ("ERROR: [youtube] x: Requested format not found", "unknown"),
    ("ERROR: [Errno 2] No such file or directory: '/data/audio/abc/original.webm.part'", "unknown"),
    ("ERROR: [youtube] abc: Video unavailable. This video does not exist.", "removed"),
    ("ERROR: [youtube] abc: This video is no longer available because the YouTube account associated "
     "with this video has been terminated.", "removed"),
])
def test_classify_error_precise_removed(stderr, code):
    assert yt.classify_error(stderr).code == code


def test_unknown_error_masks_absolute_paths():
    err = yt.classify_error("ERROR: [Errno 2] No such file or directory: '/data/audio/abc/original.webm.part' "
                            "(see https://github.com/yt-dlp/yt-dlp/issues/1)")
    assert "/data" not in err.message and "original.webm.part" in err.message
    assert "https://github.com/yt-dlp/yt-dlp/issues/1" in err.message  # 網址不受影響
    assert "/data" not in err.detail
    assert yt.mask_paths("open /tmp/x.json failed") == "open …/x.json failed"
    assert yt.mask_paths("at /data/jobs/ ok") == "at …/ ok"


def test_failure_reason():
    assert youtube_source.failure_reason(RATE_LIMITED).startswith("HTTP 429")
    assert youtube_source.failure_reason(
        yt.classify_error("ERROR: unable to download video data: HTTP Error 404: Not Found")) == "HTTP 404"
    assert youtube_source.failure_reason(yt.YouTubeError("network", "x", 502)) == "連線失敗"


# ---- 10. 下載參數 ----


def test_download_args_restrict_extractor_and_sleep(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if "--skip-download" not in args:
            (tmp_path / "original.webm").write_bytes(b"x")
        return ytdlp.Result(0, "", "")

    monkeypatch.setattr(ytdlp, "run", fake_run)
    info = make_info(subs=["zh-TW"])
    ytdlp.download(info, tmp_path, audio=False, caption_lang="zh-TW", on_progress=lambda p: None,
                   check_cancel=None, max_bytes=100)
    ytdlp.download(info, tmp_path, audio=True, caption_lang=None, on_progress=lambda p: None,
                   check_cancel=None, max_bytes=100)
    caps, audio = calls
    for args in calls:
        i = args.index("--use-extractors")
        assert args[i + 1] == "youtube"
        assert args[args.index("--sleep-requests") + 1] == str(ytdlp.SLEEP_REQUESTS)
    assert "--skip-download" in caps and "--write-subs" in caps and "--sleep-subtitles" in caps
    assert "-f" not in caps
    assert "--write-subs" not in audio and "-f" in audio
    assert not list(tmp_path.glob("youtube.info*"))


def test_fetch_video_info_sleep_requests(monkeypatch):
    calls = []
    monkeypatch.setattr(ytdlp, "run", lambda args, **kw: calls.append(args) or ytdlp.Result(0, json.dumps({"id": VID}), ""))
    ytdlp.fetch_video_info(VID)
    ytdlp.fetch_video_info(VID, sleep_requests=1)
    assert "--sleep-requests" not in calls[0]
    assert calls[1][calls[1].index("--sleep-requests") + 1] == "1"


# ---- 14. 模型沒安裝時，字幕路線照常 ----


def test_caption_job_does_not_need_whisper(client, monkeypatch, backend):
    """conftest 模擬 Breeze 尚未安裝：有人工字幕的任務照樣建立與處理。"""
    FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"]), VID2: make_info(VID2)})
    r = client.post("/api/jobs/youtube", json={"video_id": VID, "model": "breeze-asr-25"})
    assert r.status_code == 200, r.text
    done = run_next(client)
    assert done["status"] == "done", done["error"]
    assert done["caption_source"] == "manual:zh-TW"
    assert "transcribe" not in [c[0] for c in backend.calls]
    # 沒有人工字幕（要轉錄）時照樣擋下
    r = client.post("/api/jobs/youtube", json={"video_id": VID2, "model": "breeze-asr-25"})
    assert r.status_code == 400 and "尚未安裝" in r.json()["detail"]
    # 不用字幕也擋下
    r = client.post("/api/jobs/youtube", json={"video_id": VID, "model": "breeze-asr-25", "use_captions": False})
    assert r.status_code == 400
    # 講者人數仍要檢查
    r = client.post("/api/jobs/youtube", json={"video_id": VID, "model": "breeze-asr-25", "num_speakers": 50})
    assert r.status_code == 400


def test_caption_job_fails_clearly_when_fallback_needs_missing_model(client, monkeypatch, backend):
    FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])}, caption_error=RATE_LIMITED)
    job = client.post("/api/jobs/youtube", json={"video_id": VID, "model": "breeze-asr-25"}).json()
    done = run_next(client)
    assert done["status"] == "failed"
    assert "breeze-asr-25 無法使用" in done["error"] and "429" in done["error"]
    assert "transcribe" not in [c[0] for c in backend.calls]
    assert "已改用" not in done["error"] and queue.get(job["id"])["status"] == "failed"


# ---- 8. 下載計時 ----


def test_download_seconds_include_info_fetch(data_dir, monkeypatch):
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])})
    real_fetch = fake.fetch_video_info

    def slow_fetch(video_id, **kw):
        time.sleep(0.3)
        return real_fetch(video_id, **kw)

    monkeypatch.setattr(ytdlp, "fetch_video_info", slow_fetch)
    youtube_cache.clear()
    prep = youtube_source.prepare(_prepare_job(), lambda *a: None, lambda: None, max_bytes=10**9)
    assert prep.download_seconds is not None and prep.download_seconds >= 0.3
    assert prep.network_seconds >= prep.download_seconds
    youtube_cache.clear()


def test_caption_only_route_excludes_network_time_from_process_seconds(client, monkeypatch):
    """只下載字幕（重新轉錄）時，取得資訊與下載字幕的時間也要從處理秒數扣掉。"""
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"])})
    job = create(client, use_captions=False)
    assert run_next(client)["status"] == "done"
    real_fetch = fake.fetch_video_info

    def slow_fetch(video_id, **kw):
        time.sleep(0.6)
        return real_fetch(video_id, **kw)

    monkeypatch.setattr(ytdlp, "fetch_video_info", slow_fetch)
    youtube_cache.clear()
    client.post(f"/api/jobs/{job['id']}/rerun", json={"use_captions": True})
    done = run_next(client)
    assert done["caption_source"] == "manual:zh-TW"
    assert done["process_seconds"] < 0.5


# ---- 15. yt-dlp 版本：失敗不要永久快取 ----


def test_ytdlp_version_retries_after_failure(monkeypatch):
    monkeypatch.setattr(ytdlp, "_version_cache", [])
    monkeypatch.setattr(ytdlp, "_version_failed_at", [0.0])
    monkeypatch.setattr(ytdlp, "VERSION_RETRY_SECONDS", 0)
    results = [subprocess.TimeoutExpired("yt-dlp", 30),
               subprocess.CompletedProcess([], 0, stdout="2026.10.26\n", stderr="")]
    calls = []

    def fake_run(*a, **kw):
        calls.append(1)
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ytdlp.subprocess, "run", fake_run)
    assert ytdlp.version() is None
    assert ytdlp.version() == "2026.10.26"
    assert ytdlp.version() == "2026.10.26" and len(calls) == 2  # 成功後才快取


def test_ytdlp_version_failure_is_rate_limited(monkeypatch):
    monkeypatch.setattr(ytdlp, "_version_cache", [])
    monkeypatch.setattr(ytdlp, "_version_failed_at", [0.0])
    calls = []
    monkeypatch.setattr(ytdlp.subprocess, "run", lambda *a, **kw: calls.append(1) or subprocess.CompletedProcess(
        [], 1, stdout="", stderr="boom"))
    assert ytdlp.version() is None and ytdlp.version() is None
    assert len(calls) == 1  # 60 秒內不重試
