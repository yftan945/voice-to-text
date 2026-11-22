"""YouTube 來源：網址解析與驗證、字幕語言選擇、VTT／SRT 解析、講者套用到字幕、字幕路線不跑 ASR、
下載錯誤訊息、播放清單展開（mock yt-dlp）、建立任務與 worker 流程、重新轉錄、估算、migration。"""
from __future__ import annotations

import json
import sqlite3
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import db, storage, youtube_cache
from app import dictionary as dict_mod
from app import youtube as yt
from app.api import youtube as yt_api
from app.config import settings
from app.jobs import estimate, queue
from app.main import create_app
from app.pipeline import captions, runner, ytdlp
from app.pipeline.errors import Canceled

from fakes import FakeBackend, patch_audio

VID = "abcDEF12_-x"
VID2 = "zyxWVU98-_a"
VID3 = "q1w2e3r4t5y"

# ---- 網址解析 ----


@pytest.mark.parametrize("url,kind,vid,pl", [
    (f"https://www.youtube.com/watch?v={VID}", "video", VID, None),
    (f"http://youtube.com/watch?feature=share&v={VID}&t=30s", "video", VID, None),
    (f"https://m.youtube.com/watch?v={VID}", "video", VID, None),
    (f"https://music.youtube.com/watch?v={VID}", "video", VID, None),
    (f"https://youtu.be/{VID}?si=abc", "video", VID, None),
    (f"youtu.be/{VID}", "video", VID, None),
    (f"www.youtube.com/watch?v={VID}", "video", VID, None),
    (f"https://www.youtube.com/shorts/{VID}", "video", VID, None),
    (f"https://www.youtube.com/live/{VID}?feature=shared", "video", VID, None),
    (f"https://www.youtube.com/embed/{VID}", "video", VID, None),
    (f"https://www.youtube-nocookie.com/embed/{VID}", "video", VID, None),
    ("https://www.youtube.com/playlist?list=PLc8ZkR-b4Z5hi-BEuUV14mmInxjipIftF", "playlist", None,
     "PLc8ZkR-b4Z5hi-BEuUV14mmInxjipIftF"),
    (f"https://www.youtube.com/watch?v={VID}&list=PLabc123", "playlist", VID, "PLabc123"),
    # 合輯（RD 開頭）有影片 ID 時只當成單一影片
    (f"https://www.youtube.com/watch?v={VID}&list=RD{VID}", "video", VID, None),
])
def test_parse_url(url, kind, vid, pl):
    p = yt.parse_url(url)
    assert p.kind == kind and p.video_id == vid and p.playlist_id == pl
    assert p.url.startswith("https://www.youtube.com/")


@pytest.mark.parametrize("url", [
    f"https://evil.com/watch?v={VID}",
    f"https://youtube.com.evil.com/watch?v={VID}",
    f"https://evilyoutube.com/watch?v={VID}",
    f"https://youtube.com@evil.com/watch?v={VID}",
    f"https://user:pw@www.youtube.com/watch?v={VID}",
    f"https://www.youtube.com:8080/watch?v={VID}",
    f"ftp://www.youtube.com/watch?v={VID}",
    f"file:///etc/passwd?v={VID}",
    f"http://127.0.0.1/watch?v={VID}",
    f"http://192.168.1.10:8810/watch?v={VID}",
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/channel/UC123",
    "https://www.youtube.com/",
    "",
    "not a url",
])
def test_parse_url_rejects_non_youtube(url):
    with pytest.raises(yt.InvalidUrl):
        yt.parse_url(url)


def test_canonical_url_never_uses_user_input():
    p = yt.parse_url(f"https://youtu.be/{VID}?x=http://169.254.169.254/")
    assert p.url == f"https://www.youtube.com/watch?v={VID}"


def test_split_lines():
    text = f" https://youtu.be/{VID}\n\nhttps://youtu.be/{VID}\r\nhttps://youtu.be/{VID2}, https://youtu.be/{VID3} "
    assert yt.split_lines(text) == [f"https://youtu.be/{VID}", f"https://youtu.be/{VID2}", f"https://youtu.be/{VID3}"]


# ---- 字幕語言 ----


@pytest.mark.parametrize("langs,original,expected", [
    (["en", "zh-Hans", "zh", "zh-Hant", "zh-TW"], None, "zh-TW"),
    (["en", "zh-Hans", "zh", "zh-Hant"], None, "zh-Hant"),
    (["en", "zh-Hans", "zh"], None, "zh"),
    (["en", "zh-Hans"], None, "zh-Hans"),
    (["ja", "en-US"], "ja", "en-US"),  # en 優先於原始語言
    (["ja", "ko"], "ja", "ja"),       # 最後才是影片原始語言
    (["ja", "ko"], None, None),         # 都不符合：用模型轉錄
    (["zh-HK", "zh-CN"], None, "zh-HK"),
    (["zh-TW-x1", "zh-Hant"], None, "zh-TW-x1"),
    (["live_chat"], None, None),
    ([], "zh", None),
])
def test_choose_caption_priority(langs, original, expected):
    assert yt.choose_caption(langs, original) == expected


def test_automatic_captions_are_not_used():
    info = {"id": VID, "title": "t", "subtitles": {}, "automatic_captions": {"zh-TW": [{"ext": "vtt"}]},
            "language": "zh-TW"}
    s = yt.video_summary(info)
    assert s["manual_captions"] == [] and s["caption_lang"] is None
    info["subtitles"] = {"en": [{"ext": "vtt"}], "live_chat": [{"ext": "json"}]}
    s = yt.video_summary(info)
    assert s["manual_captions"] == ["en"] and s["caption_lang"] == "en"


def test_caption_label():
    assert yt.caption_label("zh-TW") == "YouTube 人工字幕（台灣繁體中文）"
    assert yt.caption_label("en-US") == "YouTube 人工字幕（英文）"
    assert yt.caption_label("xx") == "YouTube 人工字幕（xx）"


# ---- VTT／SRT 解析 ----

VTT = """WEBVTT
Kind: captions
Language: zh-TW

STYLE
::cue { color: white }

NOTE 這是註解
不應該出現 --> 在結果

00:00:00.150 --> 00:00:02.550 align:start position:0%
在MVP那邊也講過 就是上去

1
00:00:02.778 --> 00:00:04.338
<c.colorE5E5E5>因為是一人出局滿壘</c>

00:00:04.338 --> 00:00:04.348
因為是一人出局滿壘

00:00:04.338 --> 00:00:05.910
因為是一人出局滿壘
然後遇到<i>鏞基</i>

00:00:05.950 --> 00:00:07.122
Tom &amp; Jerry &lt;3&gt;&nbsp;yes

00:00:07.200 --> 00:00:08.000
Tom &amp; Jerry &lt;3&gt;&nbsp;yes

01:00:08.000 --> 01:00:09.500
<v 主持人>[音樂]</v>
"""


def test_parse_vtt_dedupes_and_strips_markup():
    segs = captions.parse(VTT, "vtt")
    assert [s["text"] for s in segs] == [
        "在MVP那邊也講過，就是上去",   # 中文之間的空白換成「，」
        "因為是一人出局滿壘",            # 標記拿掉；10 毫秒的快照 cue 略過
        "然後遇到鏞基",                  # 捲動式字幕：重複的上一行去掉
        "Tom & Jerry <3> yes",           # 實體還原，英文空白保留
    ]
    assert segs[0]["start"] == 0.15 and segs[0]["end"] == 2.55
    assert segs[3]["end"] == 8.0  # 連續相同文字合併，延長結束時間
    assert all("words" not in s for s in segs)


def test_parse_vtt_inline_word_times():
    text = ("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n"
            "hello<00:00:02.000><c> big</c><00:00:03.000><c> world</c>\n")
    (seg,) = captions.parse(text, "vtt")
    assert seg["text"] == "hello big world"
    assert [(w["text"], w["start"], w["end"]) for w in seg["words"]] == [
        ("hello", 1.0, 2.0), (" big", 2.0, 3.0), (" world", 3.0, 4.0)]


def test_parse_srt():
    text = ("﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n<font color=\"#fff\">第一句</font>\r\n\r\n"
            "2\r\n00:00:02,500 --> 00:00:04,000\r\n第一句\r\n第二句 <i>好</i>\r\n\r\n"
            "3\r\n00:00:04,000 --> 00:00:05,000\r\n第二句 好\r\n\r\n")
    segs = captions.parse(text, "srt")
    assert [(s["start"], s["end"], s["text"]) for s in segs] == [
        (1.0, 2.5, "第一句"), (2.5, 5.0, "第二句，好")]


def test_parse_file_detects_format(tmp_path):
    p = tmp_path / "captions.zh-TW.vtt"
    p.write_text("WEBVTT\n\n00:01.000 --> 00:02.000\n短的時間格式\n", encoding="utf-8")
    assert captions.parse_file(p) == [{"start": 1.0, "end": 2.0, "text": "短的時間格式"}]


# ---- 講者套到字幕段落 ----

TURNS = [(0.0, 1.0, "A"), (1.0, 2.2, "B"), (2.2, 3.0, "A"), (5.0, 9.0, "C")]


def test_majority_speaker_sums_overlap_per_speaker():
    # 0.5–3.0：A 0.5+0.8=1.3 秒、B 1.2 秒 → A（單看最長的一段會是 B）
    assert captions.majority_speaker(0.5, 3.0, TURNS) == "A"
    assert captions.majority_speaker(1.1, 2.0, TURNS) == "B"
    assert captions.majority_speaker(3.5, 4.0, TURNS) == "A"  # 沒重疊：最近的講者（2 秒內）


def test_caption_segment_without_word_times_is_not_split():
    segs = [{"start": 0.5, "end": 3.0, "text": "一整段字幕"}, {"start": 5.5, "end": 6.0, "text": "下一段"},
            {"start": 20.0, "end": 21.0, "text": "遠離所有講者"}]
    out = captions.assign_caption_speakers(segs, TURNS)
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "一整段字幕"), ("C", "下一段"), ("C", "遠離所有講者")]
    assert [s["id"] for s in out] == [0, 1, 2]


def test_caption_segment_with_word_times_is_split():
    seg = {"start": 0.0, "end": 2.0, "text": "你好我是",
           "words": [{"text": "你好", "start": 0.0, "end": 0.9}, {"text": "我是", "start": 1.1, "end": 2.0}]}
    out = captions.assign_caption_speakers([seg], TURNS)
    assert [(s["speaker"], s["text"]) for s in out] == [("A", "你好"), ("B", "我是")]


def test_caption_speakers_without_diarization():
    out = captions.assign_caption_speakers([{"start": 0.0, "end": 1.0, "text": "x"}], None)
    assert out == [{"start": 0.0, "end": 1.0, "speaker": None, "text": "x", "id": 0}]


# ---- 字幕路線不跑 ASR ----


def _input(tmp_path, **kw):
    src = tmp_path / "original.webm"
    src.write_bytes(b"x")
    base = dict(job_id="yt1", source=src, playback=tmp_path / "playback.m4a",
                model=settings.model_option("large-v3"), diarize=True, num_speakers=None,
                dictionary=dict_mod.parse("人工智慧 = 人工只能"), voiceprints=[])
    base.update(kw)
    return runner.JobInput(**base)


CAPTION_SEGS = [{"start": 0.0, "end": 1.9, "text": "大家好，我是亞當，住在台北"},
                {"start": 2.5, "end": 4.0, "text": "人工只能很好"}]


def test_runner_caption_route_skips_asr_and_align(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    backend = FakeBackend(turns=FakeBackend.CAPTION_TURNS)
    inp = _input(tmp_path, captions=CAPTION_SEGS, caption_lang="zh-TW",
                 source_info={"type": "youtube", "video_id": VID})
    progress = []
    out = runner.run(inp, backend, lambda k, label, p: progress.append((k, label, p)), lambda: None)
    kinds = [c[0] for c in backend.calls]
    assert "transcribe" not in kinds and "align" not in kinds and "diarize" in kinds
    t = out.transcript
    # 第一段 0–1.9 秒：SPEAKER_02（0.8–2.0）重疊 1.1 秒 > SPEAKER_05 的 0.8 秒；講者依出現順序重新編號
    assert [(s["speaker"], s["text"]) for s in t["segments"]] == [
        ("SPEAKER_00", "大家好，我是亞當，住在台北"),   # 繁體字幕不跑 OpenCC：「台北」不會變「臺北」
        ("SPEAKER_01", "人工智慧很好"),       # 字典替換照常
    ]
    assert t["model"] is None
    assert t["text_source"] == {"type": "youtube_captions", "lang": "zh-TW", "label": "YouTube 人工字幕（台灣繁體中文）"}
    assert t["source"]["video_id"] == VID
    assert t["speaker_embeddings"]  # 聲紋照常計算
    labels = [label for _, label, _ in progress]
    assert "讀取 YouTube 字幕中" in labels and "語音轉文字中" not in labels
    values = [p for _, _, p in progress]
    assert values == sorted(values) and values[-1] == 1.0


def test_runner_model_route_records_text_source(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    out = runner.run(_input(tmp_path), FakeBackend(), lambda *a: None, lambda: None)
    assert out.transcript["text_source"]["type"] == "model" and out.transcript["model"] == "large-v3"


# ---- 錯誤訊息 ----


@pytest.mark.parametrize("stderr,code", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "private"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.",
     "age_restricted"),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video, "
     "and other exclusive perks.", "members_only"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "removed"),
    ("ERROR: [youtube] abc: This video is unavailable", "removed"),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "geo_restricted"),
    ("ERROR: [youtube] abc: This video is not available from your location due to geo restriction",
     "geo_restricted"),
    ("ERROR: [youtube] abc: This live event will begin in 3 hours.", "live_not_ended"),
    ("ERROR: [youtube] abc: Premieres in 10 hours", "live_not_ended"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies", "bot_check"),
    ("ERROR: [youtube] abc: This content isn't available, try again later.", "rate_limited"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "forbidden"),
    ("ERROR: [youtube] abc: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name "
     "resolution>", "network"),
    ("ERROR: [youtube:tab] PLx: YouTube said: The playlist does not exist.", "playlist_missing"),
    ("WARNING: something\nERROR: something totally new", "unknown"),
])
def test_classify_error(stderr, code):
    err = yt.classify_error(stderr)
    assert err.code == code
    assert err.message  # 每一種都有給使用者看的說明
    if code == "unknown":
        assert "something totally new" in err.message


@pytest.mark.parametrize("live,code", [("is_live", "live_not_ended"), ("is_upcoming", "live_not_ended"),
                                       ("post_live", "live_not_ended")])
def test_check_playable_live(live, code):
    with pytest.raises(yt.YouTubeError) as e:
        yt.check_playable({"live_status": live})
    assert e.value.code == code and "直播" in e.value.message
    yt.check_playable({"live_status": "was_live"})  # 已經結束的直播可以下載


def test_safe_filename():
    assert yt.safe_filename("a/b: c.v1.2?") == "a b c.v1.2"
    assert yt.safe_filename("///") == "transcript"


# ---- yt-dlp 子行程 ----


def test_ytdlp_cancel_kills_process(monkeypatch):
    monkeypatch.setattr(ytdlp, "YTDLP", [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(ytdlp, "COMMON_ARGS", [])
    t0 = time.monotonic()
    calls = []

    def check_cancel():
        calls.append(1)
        if len(calls) >= 2:
            raise Canceled()

    with pytest.raises(Canceled):
        ytdlp.run([], timeout=60, check_cancel=check_cancel)
    assert time.monotonic() - t0 < 10


def test_ytdlp_timeout_does_not_hang(monkeypatch):
    monkeypatch.setattr(ytdlp, "YTDLP", [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(ytdlp, "COMMON_ARGS", [])
    t0 = time.monotonic()
    with pytest.raises(yt.YouTubeError) as e:
        ytdlp.run([], timeout=1)
    assert e.value.code == "timeout" and time.monotonic() - t0 < 10


def test_ytdlp_progress_lines(monkeypatch):
    script = ("print('V2TPROG 100 1000 NA downloading'); print('V2TPROG 500 NA 1000 downloading');"
              "print('other'); import sys; print('ERROR: [youtube] x: Private video', file=sys.stderr); sys.exit(1)")
    monkeypatch.setattr(ytdlp, "YTDLP", [sys.executable, "-c", script])
    monkeypatch.setattr(ytdlp, "COMMON_ARGS", [])
    seen = []
    res = ytdlp.run([], timeout=30, on_line=lambda line: seen.append(ytdlp.parse_progress(line)))
    assert seen == [(100.0, 1000.0), (500.0, 1000.0), None]
    assert res.returncode == 1 and yt.classify_error(res.stderr).code == "private"


def test_fetch_playlist_uses_canonical_url_and_youtube_extractor(monkeypatch):
    got = {}

    def fake_run(args, **kw):
        got["args"] = args
        return ytdlp.Result(0, json.dumps({"_type": "playlist", "entries": []}), "")

    monkeypatch.setattr(ytdlp, "run", fake_run)
    ytdlp.fetch_playlist("PLabc")
    assert got["args"][-1] == "https://www.youtube.com/playlist?list=PLabc"
    assert "--use-extractors" in got["args"] and "youtube:tab" in got["args"]


# ---- API 與 worker（mock yt-dlp） ----


def make_info(vid=VID, *, title="測試影片 v1.2", subs=None, auto=None, live="not_live", duration=4, language=None):
    return {"id": vid, "title": title, "channel": "測試頻道", "duration": duration, "live_status": live,
            "availability": "public", "language": language,
            "subtitles": {k: [{"ext": "vtt"}] for k in (subs or [])},
            "automatic_captions": {k: [{"ext": "vtt"}] for k in (auto or [])}}


VTT_ZH = "WEBVTT\n\n00:00:00.000 --> 00:00:01.900\n大家好 我是亚当\n\n00:00:02.500 --> 00:00:04.000\n人工只能很好\n"


class FakeYtdlp:
    """取代 ytdlp.fetch_video_info／fetch_playlist／download。"""

    def __init__(self, monkeypatch, infos, *, download_error=None, caption_error=None, playlist=None,
                 caption_text=None):
        self.infos = infos
        self.download_error = download_error  # 下載音訊失敗
        self.caption_error = caption_error    # 下載字幕失敗
        self.caption_text = caption_text or VTT_ZH
        self.playlist = playlist
        self.fetches: list[str] = []
        self.downloads: list[dict] = []
        monkeypatch.setattr(ytdlp, "fetch_video_info", self.fetch_video_info)
        monkeypatch.setattr(ytdlp, "fetch_playlist", self.fetch_playlist)
        monkeypatch.setattr(ytdlp, "download", self.download)

    def fetch_video_info(self, video_id, *, check_cancel=None, timeout=90, sleep_requests=None):
        self.fetches.append(video_id)
        info = self.infos.get(video_id)
        if isinstance(info, Exception):
            raise info
        if info is None:
            raise yt.classify_error("ERROR: [youtube] x: This video is unavailable")
        return info

    def fetch_playlist(self, playlist_id, *, limit=200, timeout=120):
        if self.playlist is None:
            raise yt.classify_error("ERROR: [youtube:tab] x: YouTube said: The playlist does not exist.")
        return self.playlist

    def download(self, info, dest_dir, *, audio, caption_lang, on_progress, check_cancel, max_bytes,
                 duration=None):
        self.downloads.append({"video_id": info["id"], "audio": audio, "caption_lang": caption_lang})
        on_progress(0.5)
        if check_cancel:
            check_cancel()
        if audio and self.download_error:
            raise self.download_error
        if caption_lang and self.caption_error:
            raise self.caption_error
        dest_dir.mkdir(parents=True, exist_ok=True)
        if audio:
            (dest_dir / "original.webm").write_bytes(b"opus-audio")
        if caption_lang:
            (dest_dir / f"captions.{caption_lang}.vtt").write_text(self.caption_text, encoding="utf-8")
        on_progress(1.0)


@pytest.fixture()
def backend():
    return FakeBackend(turns=FakeBackend.CAPTION_TURNS)


@pytest.fixture()
def client(data_dir, monkeypatch, backend):
    patch_audio(monkeypatch)
    youtube_cache.clear()
    app = create_app(backend=backend, start_worker=False)
    with TestClient(app) as c:
        yield c


def run_next(client):
    job = queue.claim_next()
    assert job is not None
    client.app.state.worker.process(job)
    return queue.get(job["id"])


def create(client, video_id=VID, **kw):
    r = client.post("/api/jobs/youtube", json={"video_id": video_id, "model": "large-v3", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_resolve_videos_playlists_and_invalid(client, monkeypatch):
    playlist = {"_type": "playlist", "id": "PLabc", "title": "移民", "channel": "頻道", "playlist_count": 4,
                "entries": [
                    {"id": VID, "title": "第一支", "duration": 139, "channel": "頻道"},
                    {"id": VID2, "title": "[Private video]", "duration": None},
                    {"id": VID3, "title": "直播", "duration": None, "live_status": "is_upcoming"},
                    {"id": "bad", "title": "格式錯誤的 ID 略過"},
                ]}
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=["zh-TW"], auto=["en"])}, playlist=playlist)
    text = (f"https://youtu.be/{VID}\nhttps://evil.example.com/watch?v={VID}\n"
            "https://www.youtube.com/playlist?list=PLabc\n"
            f"https://youtu.be/{VID2}")
    r = client.post("/api/youtube/resolve", json={"text": text})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["kind"] for i in items] == ["video", "invalid", "playlist", "video"]
    v = items[0]["video"]
    assert v["video_id"] == VID and v["manual_captions"] == ["zh-TW"] and v["caption_lang"] == "zh-TW"
    assert v["error"] is None
    assert "只接受 YouTube" in items[1]["error"]
    pl = items[2]["playlist"]
    assert pl["title"] == "移民" and pl["count"] == 4
    assert [(e["video_id"], e["unavailable"]) for e in pl["entries"]] == [(VID, False), (VID2, True), (VID3, True)]
    assert pl["entries"][0]["duration"] == 139.0
    assert items[3]["error"] == "影片已被下架、刪除或不存在。請確認網址是否正確。"
    # 第一支影片的資訊有快取，再查不會再跑 yt-dlp
    assert client.get(f"/api/youtube/videos/{VID}").json()["title"] == "測試影片 v1.2"
    assert fake.fetches.count(VID) == 1


def test_resolve_playlist_error(client, monkeypatch):
    FakeYtdlp(monkeypatch, {})
    items = client.post("/api/youtube/resolve", json={"text": "https://www.youtube.com/playlist?list=PLnope"}).json()[
        "items"]
    assert "播放清單不存在" in items[0]["error"]


def test_video_endpoint_errors(client, monkeypatch):
    FakeYtdlp(monkeypatch, {VID: yt.classify_error("ERROR: Private video. Sign in"),
                            VID2: make_info(VID2, live="is_live")})
    r = client.get(f"/api/youtube/videos/{VID}")
    assert r.status_code == 422 and "私人影片" in r.json()["detail"]
    assert client.get("/api/youtube/videos/not-an-id").status_code == 400
    # 直播中：資訊照樣回傳，但標示錯誤；不能建立任務
    assert "直播還在進行中" in client.get(f"/api/youtube/videos/{VID2}").json()["error"]
    r = client.post("/api/jobs/youtube", json={"video_id": VID2})
    assert r.status_code == 422 and "直播" in r.json()["detail"]


def test_youtube_job_with_manual_captions(client, monkeypatch, backend):
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=["en", "zh-TW"], auto=["zh-TW", "ja"])})
    job = create(client, batch_id="b1")
    assert job["source"] == "youtube" and job["video_id"] == VID and job["batch_id"] == "b1"
    assert job["filename"] == "測試影片 v1.2" and job["yt_channel"] == "測試頻道"
    assert job["source_url"] == f"https://www.youtube.com/watch?v={VID}"
    assert job["yt_manual_captions"] == ["en", "zh-TW"] and job["use_captions"] is True
    assert job["duration"] == 4 and job["download_pending"] is True and job["caption_source"] is None
    assert job["estimate_seconds"] is not None

    done = run_next(client)
    assert done["status"] == "done", done["error"]
    assert done["caption_source"] == "manual:zh-TW" and done["audio_ext"] == ".webm"
    assert done["has_audio"] and done["has_playback"] and not done["download_pending"]
    assert done["download_seconds"] is not None
    # 字幕與音訊分成兩次 yt-dlp：先字幕、再音訊；影片資訊沿用建立任務時的快取，不再解析
    assert fake.downloads == [{"video_id": VID, "audio": False, "caption_lang": "zh-TW"},
                              {"video_id": VID, "audio": True, "caption_lang": None}]
    assert fake.fetches == [VID]
    kinds = [c[0] for c in backend.calls]
    assert "transcribe" not in kinds and "align" not in kinds
    t = client.get(f"/api/jobs/{job['id']}/transcript").json()["current"]
    assert t["text_source"]["label"] == "YouTube 人工字幕（台灣繁體中文）"
    # zh-TW 字幕不跑 OpenCC（「亚」也不會被轉）；中文之間的空白照常換成「，」
    assert [s["text"] for s in t["segments"]] == ["大家好，我是亚当", "人工只能很好"]
    assert len(t["speakers"]) == 2
    assert not list(storage.job_dir(job["id"]).glob("youtube.info*"))

    # 匯出檔名用影片標題（標題裡的「.」不會被當成副檔名）
    r = client.get(f"/api/jobs/{job['id']}/export/txt")
    assert "%E6%B8%AC%E8%A9%A6%E5%BD%B1%E7%89%87%20v1.2.txt" in r.headers["content-disposition"]

    # 重新轉錄改用模型：音訊與字幕都在，不再連 YouTube
    fetches = len(fake.fetches)
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={"model": "large-v3", "use_captions": False})
    assert r.status_code == 200 and r.json()["use_captions"] is False
    done = run_next(client)
    assert done["status"] == "done" and done["caption_source"] == "none"
    assert len(fake.fetches) == fetches and len(fake.downloads) == 2
    assert "transcribe" in [c[0] for c in backend.calls]
    # 再改回字幕
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={"model": "large-v3", "use_captions": True})
    assert r.status_code == 200
    assert run_next(client)["caption_source"] == "manual:zh-TW"
    assert len(fake.fetches) == fetches

    # 三種刪除：只刪音檔後不能重新轉錄
    assert client.delete(f"/api/jobs/{job['id']}?mode=audio").status_code == 200
    assert not storage.job_dir(job["id"]).exists()
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={})
    assert r.status_code == 409
    assert client.delete(f"/api/jobs/{job['id']}?mode=all").status_code == 200
    assert queue.get(job["id"]) is None


def test_youtube_job_auto_captions_only_uses_model(client, monkeypatch, backend):
    fake = FakeYtdlp(monkeypatch, {VID: make_info(subs=[], auto=["zh-TW", "zh-Hant"])})
    job = create(client)
    done = run_next(client)
    assert done["status"] == "done" and done["caption_source"] == "none"
    assert fake.downloads == [{"video_id": VID, "audio": True, "caption_lang": None}]
    assert "transcribe" in [c[0] for c in backend.calls]
    t = client.get(f"/api/jobs/{job['id']}/transcript").json()["current"]
    assert t["text_source"]["type"] == "model"
    # 沒有人工字幕時不能選「用字幕」重新轉錄
    r = client.post(f"/api/jobs/{job['id']}/rerun", json={"use_captions": True})
    assert r.status_code == 400 and "人工字幕" in r.json()["detail"]


def test_download_error_fails_job_and_worker_continues(client, monkeypatch):
    fake = FakeYtdlp(monkeypatch, {VID: make_info(), VID2: make_info(VID2)},
                     download_error=yt.classify_error("ERROR: unable to download video data: HTTP Error 403"))
    a = create(client)
    b = create(client, VID2)
    done_a = run_next(client)
    assert done_a["status"] == "failed" and "HTTP 403" in done_a["error"]
    assert not list(storage.job_dir(a["id"]).glob("original.*"))
    # 下一個任務照常處理；修好之後重新轉錄會重新下載
    fake.download_error = None
    assert run_next(client)["status"] == "done"
    r = client.post(f"/api/jobs/{a['id']}/rerun", json={})
    assert r.status_code == 200
    assert run_next(client)["status"] == "done"
    # 第一次：沿用快取資訊下載失敗（403 可能是資訊過期）→ 重新解析再試一次，仍失敗才標成失敗
    assert [d["video_id"] for d in fake.downloads] == [VID, VID, VID2, VID]
    assert b["id"] != a["id"]


def test_video_becomes_private_before_processing(client, monkeypatch):
    fake = FakeYtdlp(monkeypatch, {VID: make_info()})
    job = create(client)
    fake.infos[VID] = yt.classify_error("ERROR: [youtube] x: Private video. Sign in")
    youtube_cache.clear()  # 快取過期（超過 2 小時）後 worker 會重新解析
    done = run_next(client)
    assert done["status"] == "failed" and "私人影片" in done["error"]
    assert queue.get(job["id"])["status"] == "failed"


def test_cancel_during_download(client, monkeypatch):
    FakeYtdlp(monkeypatch, {VID: make_info()})
    job = create(client)
    claimed = queue.claim_next()
    queue.request_cancel(job["id"])
    client.app.state.worker.process(claimed)
    assert queue.get(job["id"])["status"] == "canceled"


def test_create_youtube_job_validation(client, monkeypatch):
    FakeYtdlp(monkeypatch, {VID: make_info()})
    assert client.post("/api/jobs/youtube", json={"video_id": VID, "model": "nope"}).status_code == 400
    assert client.post("/api/jobs/youtube", json={"video_id": VID, "batch_id": "a b"}).status_code == 400
    assert client.post("/api/jobs/youtube", json={"video_id": "../../etc"}).status_code == 400


def test_estimate_youtube_download_and_captions(data_dir):
    rates = estimate.Rates()
    model_job = queue.create(filename="m", model="large-v3", diarize=True, num_speakers=None, dictionary_ids=[],
                             audio_ext="", size_bytes=0, duration=600.0,
                             youtube={"video_id": VID, "manual_captions": [], "use_captions": True})
    cap_job = queue.create(filename="c", model="large-v3", diarize=True, num_speakers=None, dictionary_ids=[],
                           audio_ext="", size_bytes=0, duration=600.0,
                           youtube={"video_id": VID2, "manual_captions": ["zh-TW"], "use_captions": True})
    dl = estimate.DEFAULT_DOWNLOAD_OVERHEAD + estimate.DEFAULT_DOWNLOAD_RATE * 600
    assert rates.process_seconds(model_job) == pytest.approx(
        estimate.BASE_OVERHEAD + estimate.default_rate("large-v3", True) * 600 + dl)
    # 字幕路線：沒有 ASR，比模型轉錄快
    assert rates.process_seconds(cap_job) == pytest.approx(
        estimate.BASE_OVERHEAD + estimate.default_rate(estimate.CAPTIONS_KEY, True) * 600 + dl)
    assert rates.process_seconds(cap_job) < rates.process_seconds(model_job)
    # 已下載（重新轉錄）就不再算下載時間
    assert rates.download_seconds({**model_job, "download_pending": False}) == 0
    # 字幕路線不需要載入 Whisper：排第一個也不加載入時間
    info = estimate.queue_info([cap_job], rates=rates, loaded=[])
    assert info[cap_job["id"]]["estimate_seconds"] == estimate._round(rates.process_seconds(cap_job))
    assert 0.02 <= estimate.download_share(model_job, rates) <= 0.8


def test_estimate_learns_download_speed():
    rows = [{"model": "large-v3", "diarize": 1, "duration": 600.0, "process_seconds": 60.0, "load_seconds": 0,
             "caption_source": "manual:zh-TW", "download_seconds": 8.0 + 600 * 0.1}] * 5
    rates = estimate.rates_from_history(rows)
    assert rates.download_rate() > estimate.DEFAULT_DOWNLOAD_RATE
    assert rates.has_history(estimate.CAPTIONS_KEY, True) and not rates.has_history("large-v3", True)


# ---- migration ----


def test_migration_adds_youtube_columns(tmp_path, monkeypatch):
    """目前正式環境的資料庫（批次佇列版本，沒有來源欄位）：補欄位後舊任務都是 file 來源。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "v2")
    settings.ensure_dirs()
    added = ("-- 來源", "source", "video_id", "yt_", "use_captions", "caption_source", "download_")
    old = "\n".join(ln for ln in db.SCHEMA.splitlines() if not ln.strip().startswith(added))
    old = old.replace("load_seconds REAL,", "load_seconds REAL")
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(old)
    conn.execute("INSERT INTO jobs (id, filename, created_at, status, model) VALUES ('old', 'a.m4a', 1, 'done', 'large-v3')")
    conn.commit()
    conn.close()
    db.init_db()
    db.init_db()
    got = queue.get("old")
    assert got["source"] == "file" and got["video_id"] is None and got["yt_manual_captions"] == []
    assert got["use_captions"] is True and got["caption_source"] is None and got["download_pending"] is False
