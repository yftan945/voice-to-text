"""介面語言（zh-TW／en）：訊息目錄、語言判斷、REST 錯誤、SSE、逐字稿、匯出、舊資料相容。"""
import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import i18n
from app.api import jobs as jobs_api
from app.config import settings
from app.jobs import queue
from app.main import create_app
from app.pipeline.diarize import DiarizationUnavailable

from fakes import FakeBackend, patch_audio
from test_api import run_next_job, upload


@pytest.fixture()
def client(data_dir, monkeypatch):
    patch_audio(monkeypatch)
    app = create_app(backend=FakeBackend(), start_worker=False)
    with TestClient(app) as c:
        yield c


# ---- 訊息目錄 ----


def test_every_message_has_both_languages_with_same_params():
    for code, templates in i18n.MESSAGES.items():
        assert set(templates) == set(i18n.LANGS), code
        assert all(templates[lang].strip() for lang in i18n.LANGS), code
        fields = {lang: i18n.template_fields(templates[lang]) for lang in i18n.LANGS}
        assert fields["zh-TW"] == fields["en"], (code, fields)
        assert code == code.lower() and "." in code and " " not in code, code


def test_render_fallbacks():
    assert i18n.render("job.not_found", {}, "en") == "Job not found."
    assert i18n.render("job.not_found", {}, "fr") == "找不到這筆紀錄"  # 沒有這個語言 → zh-TW
    assert i18n.render("no.such.code", {}, "en") == "no.such.code"  # 沒有這個 code → code 本身
    assert i18n.render("upload.too_large", {}, "en") == "The file is larger than {limit_mb} MB."  # 缺參數不會壞


def test_nested_and_list_params():
    m = i18n.msg("youtube.captions.failed", language=i18n.msg("language.zh-tw"),
                 reason=i18n.msg("youtube.reason.rate_limited"))
    assert i18n.localize(m, "zh-TW") == "YouTube 人工字幕（台灣繁體中文）下載失敗（HTTP 429，請求次數過多），已改用模型轉錄。"
    assert i18n.localize(m, "en") == ("Downloading the YouTube manual captions (Traditional Chinese (Taiwan)) "
                                      "failed (HTTP 429, too many requests), so a model was used instead.")
    items = [i18n.msg("warning.dropped_item", time="00:00:01", text="甲"),
             i18n.msg("warning.dropped_item", time="00:00:02", text="乙")]
    w = i18n.msg("warning.dropped", count=2, items=items, more="")
    assert i18n.localize(w, "zh-TW").endswith("：[00:00:01]「甲」、[00:00:02]「乙」")
    assert i18n.localize(w, "en").endswith(': [00:00:01] "甲", [00:00:02] "乙"')


def test_localize_keeps_old_plain_strings():
    assert i18n.localize("排隊中", "en") == "排隊中"
    assert i18n.localize(None, "en") is None
    assert i18n.localize('{"code": "stage.done", "params": {}}', "en") == "Done"  # 資料庫裡的 JSON 字串
    assert i18n.localize('{"code": broken', "en") == '{"code": broken'
    # 參數值裡的大括號原樣保留，不會被當成模板
    assert i18n.localize(i18n.msg("diarize.failed", error="{oops}"), "en") == "Speaker identification failed: {oops}"


class _Req:
    def __init__(self, query=None, headers=None):
        self.query_params = query or {}
        self.headers = headers or {}


@pytest.mark.parametrize("query,accept,expected", [
    (None, None, "zh-TW"),
    ("en", None, "en"),
    ("en-US", "zh-TW", "en"),
    ("zh", "en", "zh-TW"),
    ("zh-Hant-TW", None, "zh-TW"),
    ("", "en-US,en;q=0.9", "en"),
    (None, "zh-TW,zh;q=0.9,en;q=0.8", "zh-TW"),
    (None, "zh-CN", "zh-TW"),
    (None, "ja-JP,ja;q=0.9", "en"),       # 不是中文 → en
    (None, "en;q=0.5,zh-TW;q=0.9", "zh-TW"),  # 照 q 值
    (None, "zh-TW;q=0,en;q=0.1", "en"),   # q=0 表示不要
    (None, "*", "zh-TW"),
    ("fr", None, "en"),
])
def test_resolve_lang(query, accept, expected):
    req = _Req({"lang": query} if query is not None else {}, {"accept-language": accept} if accept else {})
    assert i18n.resolve_lang(req) == expected
    assert i18n.resolve_lang(None) == "zh-TW"


# ---- REST 錯誤 ----


def test_api_error_payload_follows_language(client):
    r = client.get("/api/jobs/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "找不到這筆紀錄", "code": "job.not_found", "params": {}}
    r = client.get("/api/jobs/nope?lang=en")
    assert r.json() == {"detail": "Job not found.", "code": "job.not_found", "params": {}}
    r = client.get("/api/jobs/nope", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert r.json()["detail"] == "Job not found."
    r = client.get("/api/jobs/nope", headers={"Accept-Language": "zh-TW"})
    assert r.json()["detail"] == "找不到這筆紀錄"


def test_api_error_with_params(client):
    files = {"file": ("a.m4a", b"x", "audio/mp4")}
    r = client.post("/api/jobs?lang=en", files=files, data={"model": "large-v3", "num_speakers": "50"})
    assert r.status_code == 400
    assert r.json() == {"detail": "The number of speakers must be between 1 and 20.",
                        "code": "job.num_speakers.range", "params": {"min": 1, "max": 20}}
    r = client.post("/api/jobs?lang=en", files={"file": ("a.m4a", b"", "audio/mp4")})
    assert r.json()["detail"] == "The file is empty." and r.json()["code"] == "upload.empty"
    # 模型名稱是巢狀訊息：跟著語言
    r = client.post("/api/jobs?lang=en", files=files, data={"model": "breeze-asr-25"})
    assert r.json()["detail"] == "The Accurate (Breeze-ASR-25) model isn't installed."
    r = client.post("/api/jobs", files=files, data={"model": "breeze-asr-25"})
    assert r.json()["detail"] == "模型 精準（Breeze-ASR-25） 尚未安裝"


def test_upload_limit_middleware_follows_language(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    big = b"x" * (1024 * 1024 + 10)
    r = client.post("/api/jobs?lang=en", files={"file": ("a.m4a", big, "audio/mp4")})
    assert r.status_code == 413
    assert r.json() == {"detail": "The file is larger than 0 MB.", "code": "upload.too_large",
                        "params": {"limit_mb": 0}}
    r = client.post("/api/jobs", files={"file": ("a.m4a", big, "audio/mp4")})
    assert r.json()["detail"] == "檔案超過 0 MB"


def test_localized_exceptions_become_api_errors(client):
    job = upload(client, diarize="false")
    run_next_job(client)
    r = client.post(f"/api/jobs/{job['id']}/transcript/edit?lang=en", json={"op": "nope"})
    assert r.status_code == 400
    assert r.json() == {"detail": "Unsupported edit operation: nope", "code": "transcript.edit.unsupported",
                        "params": {"op": "nope"}}
    r = client.post(f"/api/jobs/{job['id']}/transcript/edit?lang=en", json={"op": "set_text"})
    assert r.json()["code"] == "transcript.edit.invalid"
    r = client.post(f"/api/jobs/{job['id']}/move?lang=en", json={"to": "front"})
    assert r.status_code == 409 and r.json()["detail"] == "Only queued jobs can be reordered."
    r = client.post("/api/dictionaries/1/rules?lang=en", json={"wrong": "a", "correct": "a"})
    assert r.status_code == 404 and r.json()["detail"] == "Dictionary not found."
    d = client.post("/api/dictionaries", json={"name": "d"}).json()
    r = client.post(f"/api/dictionaries/{d['id']}/rules?lang=en", json={"wrong": "a", "correct": "a"})
    assert r.status_code == 400 and r.json()["code"] == "dictionary.rule.invalid"


def test_models_endpoint_in_english(client):
    models = {m["id"]: m for m in client.get("/api/models?lang=en").json()["models"]}
    assert models["large-v3"]["label"] == "Multilingual (Whisper large-v3)"
    assert models["breeze-asr-25"]["description"].startswith("Best for Taiwanese Mandarin")
    zh = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert zh["large-v3"]["label"] == "多語（Whisper large-v3）"


# ---- 任務的 stage／error／warnings ----


def test_job_texts_follow_language(client):
    job = upload(client, diarize="false")
    q = client.get(f"/api/jobs/{job['id']}?lang=en").json()
    assert q["stage"] == "Queued" and q["stage_code"] == "stage.queued"
    assert client.get(f"/api/jobs/{job['id']}").json()["stage"] == "排隊中"
    run_next_job(client)
    done = client.get(f"/api/jobs/{job['id']}?lang=en").json()
    assert done["stage"] == "Done" and done["stage_code"] == "stage.done" and done["error"] is None
    # 資料庫存的是 code＋params
    assert queue.get(job["id"])["stage"] == {"code": "stage.done", "params": {}}


def test_failed_job_error_and_warnings_in_english(client):
    client.app.state.worker.backend = FakeBackend(diarize_error=DiarizationUnavailable("diarize.no_token"))
    job = upload(client)
    run_next_job(client)
    en = client.get(f"/api/jobs/{job['id']}?lang=en").json()
    assert en["warnings"] == [
        "HF_TOKEN isn't set, so the pyannote speaker identification model can't be downloaded."]
    zh = client.get(f"/api/jobs/{job['id']}").json()
    assert zh["warnings"] == ["沒有設定 HF_TOKEN，無法下載 pyannote 講者辨識模型"]
    # 逐字稿裡的警告與講者辨識失敗原因也跟著語言
    t = client.get(f"/api/jobs/{job['id']}/transcript?lang=en").json()
    assert t["current"]["warnings"] == en["warnings"] and t["original"]["warnings"] == en["warnings"]
    assert t["current"]["diarization"]["error"] == en["warnings"][0]
    assert t["current"]["text_source"]["label"] == "Multilingual (Whisper large-v3)"

    queue.update(job["id"], status="failed", error=i18n.msg("gpu.busy"))
    assert client.get(f"/api/jobs/{job['id']}?lang=en").json()["error"] == "The GPU is busy. Try again later."
    assert client.get(f"/api/jobs/{job['id']}").json()["error"] == "GPU 忙碌，請稍後重試"


def test_old_plain_strings_are_shown_as_is(client):
    """舊資料庫裡已經存好的中文字串照原樣顯示（不需要 migration）。"""
    job = upload(client, diarize="false")
    queue.update(job["id"], status="failed", stage="失敗", error="舊的錯誤訊息", warnings=["舊的警告"])
    got = client.get(f"/api/jobs/{job['id']}?lang=en").json()
    assert got["stage"] == "失敗" and got["stage_code"] is None
    assert got["error"] == "舊的錯誤訊息" and got["warnings"] == ["舊的警告"]
    listed = {j["id"]: j for j in client.get("/api/jobs?lang=en").json()}
    assert listed[job["id"]]["error"] == "舊的錯誤訊息"


def test_speaker_summary_default_names_follow_language(client):
    job = upload(client)
    run_next_job(client)
    client.post(f"/api/jobs/{job['id']}/transcript/edit", json={"op": "rename_speaker", "speaker": "SPEAKER_01",
                                                                "name": "主持人"})
    en = {j["id"]: j for j in client.get("/api/jobs?lang=en").json()}[job["id"]]
    assert [s["name"] for s in en["speaker_summary"]["speakers"]] == ["Speaker 1", "主持人"]
    zh = {j["id"]: j for j in client.get("/api/jobs").json()}[job["id"]]
    assert [s["name"] for s in zh["speaker_summary"]["speakers"]] == ["講者 1", "主持人"]


# ---- SSE ----


class _FakeRequest:
    """is_disconnected 第 n 次之後回 True；帶 query 與 headers，讓 SSE 依語言 render。"""

    def __init__(self, app, *, query=None, headers=None, rounds=1):
        self.app = app
        self.query_params = query or {}
        self.headers = headers or {}
        self.rounds = rounds

    async def is_disconnected(self):
        self.rounds -= 1
        return self.rounds < 0


def _collect(gen_fn, req) -> list[dict]:
    async def run():
        resp = await gen_fn(req)
        return [c if isinstance(c, str) else c.decode() async for c in resp.body_iterator]

    chunks = asyncio.run(run())
    return [json.loads(c.split("data: ", 1)[1]) for c in chunks if c.startswith("event:")]


def test_all_events_follow_language(client):
    job = upload(client, diarize="false")
    run_next_job(client)
    [en] = _collect(jobs_api.all_events, _FakeRequest(client.app, query={"lang": "en"}))
    got = {j["id"]: j for j in en["changed"]}[job["id"]]
    assert got["stage"] == "Done" and got["stage_code"] == "stage.done"
    [zh] = _collect(jobs_api.all_events, _FakeRequest(client.app))
    assert {j["id"]: j for j in zh["changed"]}[job["id"]]["stage"] == "完成"
    [al] = _collect(jobs_api.all_events, _FakeRequest(client.app, headers={"accept-language": "en-GB"}))
    assert {j["id"]: j for j in al["changed"]}[job["id"]]["stage"] == "Done"


def test_job_events_follow_language_and_signature_dedupes(client):
    job = upload(client, diarize="false")
    run_next_job(client)
    with client.stream("GET", f"/api/jobs/{job['id']}/events?lang=en") as r:
        body = "".join(r.iter_text())
    events = [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]
    assert len(events) == 1 and events[0]["stage"] == "Done" and events[0]["status"] == "done"
    # 去重用的簽章：同一份資料、同一個語言 → 相同
    a = jobs_api._signature(client.get(f"/api/jobs/{job['id']}?lang=en").json())
    b = jobs_api._signature(client.get(f"/api/jobs/{job['id']}?lang=en").json())
    assert a == b


# ---- 匯出 ----


def test_export_speaker_names_follow_language(client):
    job = upload(client)
    run_next_job(client)
    zh = client.get(f"/api/jobs/{job['id']}/export/txt").text
    assert "講者 1：" in zh and "Speaker" not in zh
    en = client.get(f"/api/jobs/{job['id']}/export/txt?lang=en").text
    assert "Speaker 1: " in en and "講者" not in en
    assert "[00:00:00] Speaker 1: " in en
    plain = client.get(f"/api/jobs/{job['id']}/export/txt?lang=en&timestamps=false").text
    assert plain.startswith("Speaker 1: ")
    srt = client.get(f"/api/jobs/{job['id']}/export/srt?lang=en").text
    assert "Speaker 1: " in srt
    vtt = client.get(f"/api/jobs/{job['id']}/export/vtt?lang=en").text
    assert "<v Speaker 1>" in vtt
    md = client.get(f"/api/jobs/{job['id']}/export/md", headers={"Accept-Language": "en"}).text
    assert md.startswith("# 會議\n") and "**Speaker 1**" in md  # 標題是檔名，不翻譯
    data = client.get(f"/api/jobs/{job['id']}/export/json?lang=en").json()
    assert data["speakers"]["SPEAKER_00"]["name"] == "Speaker 1"
    assert data["segments"][0]["speaker_name"] == "Speaker 1"
    assert data["text_source"]["label"] == "Multilingual (Whisper large-v3)"
    # 逐字稿內容本身不翻譯
    assert [s["text"] for s in data["segments"]] == [s["text"] for s in
                                                    client.get(f"/api/jobs/{job['id']}/export/json").json()["segments"]]


def test_export_default_title():
    from app import export

    t = {"segments": [{"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "hi"}], "speakers": {}}
    assert export.render(t, "md").startswith("# 逐字稿\n")
    assert export.render(t, "md", lang="en").startswith("# Transcript\n")


# ---- YouTube ----


def test_youtube_resolve_errors_follow_language(client):
    r = client.post("/api/youtube/resolve?lang=en", json={"text": "https://evil.example.com/watch?v=abc"})
    assert r.json()["items"][0]["error"] == "Only YouTube URLs (youtube.com, youtu.be) are supported."
    r = client.post("/api/youtube/resolve", json={"text": "https://evil.example.com/watch?v=abc"})
    assert r.json()["items"][0]["error"] == "只接受 YouTube 網址（youtube.com、youtu.be）"
    r = client.post("/api/youtube/resolve?lang=en", json={"text": "  "})
    assert r.status_code == 400 and r.json() == {"detail": "Paste a YouTube URL.", "code": "youtube.resolve.empty",
                                                 "params": {}}
    r = client.get("/api/youtube/videos/not-an-id?lang=en")
    assert r.status_code == 400 and r.json()["detail"] == "Invalid video ID."


# ---- 訊息代碼齊全、巢狀例外訊息、使用者文字不被當成訊息 ----

_CJK = re.compile(r"[　-〿㐀-鿿＀-￯]")


def test_every_youtube_error_and_stage_has_message():
    from app import youtube as yt
    from app.pipeline import runner

    for code, _, _ in yt._ERROR_RULES:
        assert i18n.has(f"youtube.error.{code}"), code
    for key in {**runner.STAGES, **runner.CAPTION_STAGES}:
        assert i18n.has(f"stage.{key}"), key


def test_old_strings_starting_with_brace_are_shown_as_is(client):
    """舊中文字串剛好以 { 開頭（甚至是合法 JSON）但不是訊息時，照原樣顯示。"""
    for old in ("{舊的錯誤}", '{"code": 1}', '{"code": "沒有 params 也不是代碼"', '{"x": "舊的"}'):
        assert i18n.localize(old, "en") == old
    assert i18n.localize('{"code": "no.such.code", "params": {}}', "en") == "no.such.code"
    job = upload(client, diarize="false")
    queue.update(job["id"], status="failed", stage="{失敗}", error='{"code": 1}', warnings=["{舊的警告}"])
    got = client.get(f"/api/jobs/{job['id']}?lang=en").json()
    assert got["stage"] == "{失敗}" and got["error"] == '{"code": 1}' and got["warnings"] == ["{舊的警告}"]


def test_string_params_are_never_decoded_as_messages():
    m = i18n.msg("diarize.failed", error='{"code": "job.not_found", "params": {}}')
    assert i18n.localize(m, "en") == 'Speaker identification failed: {"code": "job.not_found", "params": {}}'
    # 資料庫讀回的巢狀訊息（整則一起 dumps）參數仍是 dict，照樣依語言 render
    stored = i18n.dumps(i18n.msg("job.error.model_needed_reason", model="m",
                                 reason=i18n.msg("youtube.captions.empty.reason", language="zh-TW")))
    assert i18n.localize(stored, "en") == \
        "The m model isn't available, but this job needs it: the YouTube manual captions (zh-TW) are empty."


def test_nested_exception_messages_follow_language(client, monkeypatch):
    """對齊、聲紋失敗的例外本身是可翻譯的訊息時，英文介面不會中英混雜。"""
    class Backend(FakeBackend):
        def speaker_embeddings(self, audio, turns):
            raise DiarizationUnavailable("diarize.embedding_unavailable", model="emb-model")

    client.app.state.worker.backend = Backend(
        align_error=DiarizationUnavailable("diarize.embedding_unavailable", model="align-model"))
    job = upload(client)
    run_next_job(client)
    en = client.get(f"/api/jobs/{job['id']}?lang=en").json()["warnings"]
    assert en and not any(_CJK.search(w) for w in en), en
    assert any(w.startswith("Word alignment failed") and "Couldn't download the voiceprint model align-model" in w
               for w in en)
    assert any(w.startswith("Couldn't compute speaker voiceprints") and "emb-model" in w for w in en)
    zh = client.get(f"/api/jobs/{job['id']}").json()["warnings"]
    assert any("無法下載聲紋模型 align-model" in w for w in zh)
    # 一般例外仍是原文
    client.app.state.worker.backend = FakeBackend(align_error=RuntimeError("boom"))
    job = upload(client)
    run_next_job(client)
    assert any(w.endswith(": boom") for w in client.get(f"/api/jobs/{job['id']}?lang=en").json()["warnings"])


def test_voiceprint_embed_failure_follows_language(client, monkeypatch):
    patch_audio(monkeypatch, seconds=12.0)

    def fail(audio):
        raise DiarizationUnavailable("diarize.embedding_unavailable", model="emb-model")

    monkeypatch.setattr(client.app.state.backend, "embed", fail)
    files = {"file": ("me.m4a", b"abc", "audio/mp4")}
    r = client.post("/api/voiceprints?lang=en", files=files, data={"name": "me"})
    assert r.status_code == 500
    assert r.json()["detail"] == "Couldn't compute the voiceprint: Couldn't download the voiceprint model emb-model."
    assert not _CJK.search(r.json()["detail"])
    r = client.post("/api/voiceprints", files=files, data={"name": "me"})
    assert r.json()["detail"] == "計算聲紋失敗：無法下載聲紋模型 emb-model"


def test_edit_transcript_warnings_follow_language(client):
    client.app.state.worker.backend = FakeBackend(diarize_error=DiarizationUnavailable("diarize.no_token"))
    job = upload(client)
    run_next_job(client)
    r = client.post(f"/api/jobs/{job['id']}/transcript/edit?lang=en",
                    json={"op": "set_text", "segment_id": 0, "text": "改"})
    assert r.status_code == 200, r.text
    expected = ["HF_TOKEN isn't set, so the pyannote speaker identification model can't be downloaded."]
    assert r.json()["current"]["warnings"] == expected and r.json()["original"]["warnings"] == expected


def test_export_keeps_user_text_that_looks_like_a_message(client):
    """段落文字、講者名剛好是訊息 JSON 時，匯出原樣保留，不會被換成翻譯文字。"""
    job = upload(client)
    run_next_job(client)
    fake = '{"code": "job.not_found", "params": {}}'
    seg = client.get(f"/api/jobs/{job['id']}/transcript").json()["current"]["segments"][0]
    for op in ({"op": "set_text", "segment_id": seg["id"], "text": fake},
               {"op": "rename_speaker", "speaker": seg["speaker"], "name": fake}):
        assert client.post(f"/api/jobs/{job['id']}/transcript/edit", json=op).status_code == 200
    for fmt in ("txt", "srt", "md"):
        body = client.get(f"/api/jobs/{job['id']}/export/{fmt}?lang=en").text
        assert body.count(fake) >= 2 and "Job not found" not in body, (fmt, body)
    plain = client.get(f"/api/jobs/{job['id']}/export/txt?lang=en&timestamps=false").text
    assert plain.startswith(f"{fake}: {fake}")


def test_youtube_unknown_failure_reason_follows_language():
    from app import youtube as yt
    from app.pipeline import youtube_source

    def warning(e):
        return i18n.msg("youtube.captions.failed", language=i18n.msg("language.zh-tw"),
                        reason=youtube_source.failure_reason(e))

    e = yt.unknown_error(i18n.msg("youtube.error.no_audio_file"))
    en = i18n.localize(warning(e), "en")
    assert "no audio file was found after downloading" in en and not _CJK.search(en), en
    assert "下載後找不到音訊檔" in i18n.localize(warning(e), "zh-TW")
    en = i18n.localize(warning(yt.unknown_error("")), "en")
    assert "(unknown error)" in en and not _CJK.search(en), en
    # yt-dlp 原文照原樣
    raw = yt.classify_error("ERROR: [youtube] x: Something odd happened")
    assert youtube_source.failure_reason(raw) == raw.detail == "[youtube] x: Something odd happened"


def test_youtube_known_failure_reason_is_localized_not_code():
    """不是 unknown、也沒有簡短說明的錯誤（私人影片、會員限定）：原因用依語言顯示的說明，不露出代碼。"""
    from app import youtube as yt
    from app.pipeline import youtube_source

    errors = []
    for availability in ("private", "subscriber_only"):
        try:
            yt.check_playable({"availability": availability})
        except yt.YouTubeError as e:
            errors.append(e)
    errors.append(yt.classify_error("ERROR: [youtube] abc: Private video. Sign in if you've been granted access"))
    assert [e.code for e in errors] == ["private", "members_only", "private"]
    for e in errors:
        reason = youtube_source.failure_reason(e)
        assert reason == e.msg
        w = i18n.msg("youtube.captions.failed", language=i18n.msg("language.zh-tw"), reason=reason)
        en = i18n.localize(w, "en")
        assert not _CJK.search(en), en
        assert f"({e.code})" not in en and "members_only" not in en, en
        assert ("private video" in en) if e.code == "private" else ("channel members only" in en)
        assert "這是" in i18n.localize(w, "zh-TW")
