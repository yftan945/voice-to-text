"""faster-whisper 呼叫參數（逐 30 秒視窗語言偵測）、提示詞外洩過濾、對齊語言分組。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.pipeline import asr
from app.pipeline.align import group_by_language, segment_language
from app.pipeline.errors import Canceled


class FakeWhisper:
    """模擬 faster-whisper WhisperModel.transcribe：回傳（段落 generator, info）。"""

    def __init__(self, segments, language="zh", duration=10.0):
        self.segments = segments
        self.language = language
        self.duration = duration
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        segs = (SimpleNamespace(start=s, end=e, text=t) for s, e, t in self.segments)
        return segs, SimpleNamespace(language=self.language, duration=self.duration)


@pytest.fixture()
def asr_settings(monkeypatch):
    """預設：自動偵測語言、multilingual 開啟、沒有提示句（不受執行環境的環境變數影響）。"""
    monkeypatch.setattr(settings, "language", "")
    monkeypatch.setattr(settings, "multilingual", True)
    monkeypatch.setattr(settings, "initial_prompt", "")
    return settings


def run(model, hint_terms=(), duration=10.0, check_cancel=lambda: None):
    progress = []
    segs, lang = asr.transcribe(model, None, hint_terms=list(hint_terms), duration=duration,
                                on_progress=progress.append, check_cancel=check_cancel)
    return segs, lang, progress


# ---- 逐 30 秒視窗語言偵測 ----


def test_auto_language_uses_per_window_detection(asr_settings):
    """沒有指定語言：language=None 且 multilingual=True，每個 30 秒視窗各自偵測語言。"""
    m = FakeWhisper([(0, 1, "你好")])
    run(m)
    assert m.kwargs["language"] is None
    assert m.kwargs["multilingual"] is True
    # 不讓前一段的文字影響下一段（避免英文段被帶成中文）
    assert m.kwargs["condition_on_previous_text"] is False
    assert m.kwargs["vad_filter"] is True


def test_fixed_language_disables_multilingual(asr_settings, monkeypatch):
    """指定 ASR_LANGUAGE 時固定語言，不做逐視窗偵測。"""
    monkeypatch.setattr(settings, "language", "zh")
    m = FakeWhisper([(0, 1, "你好")])
    run(m)
    assert m.kwargs["language"] == "zh" and m.kwargs["multilingual"] is False


def test_multilingual_can_be_turned_off(asr_settings, monkeypatch):
    monkeypatch.setattr(settings, "multilingual", False)
    m = FakeWhisper([(0, 1, "你好")])
    run(m)
    assert m.kwargs["language"] is None and m.kwargs["multilingual"] is False


def test_hint_terms_go_to_hotwords(asr_settings):
    """字典提示詞放進 hotwords（每個視窗都會帶），沒有提示詞時為 None。"""
    m = FakeWhisper([(0, 1, "你好")])
    run(m, hint_terms=["人工智慧", "Kubernetes"])
    assert m.kwargs["hotwords"] == "人工智慧、Kubernetes"
    m = FakeWhisper([(0, 1, "你好")])
    run(m)
    assert m.kwargs["hotwords"] is None


def test_returns_detected_language_and_falls_back(asr_settings, monkeypatch):
    m = FakeWhisper([(0, 1, "hello")], language="en")
    assert run(m)[1] == "en"
    m = FakeWhisper([(0, 1, "你好")], language=None)
    assert run(m)[1] == "zh"
    monkeypatch.setattr(settings, "language", "ja")
    m = FakeWhisper([(0, 1, "こんにちは")], language=None)
    assert run(m)[1] == "ja"


# ---- 提示詞外洩／幻覺段落過濾 ----


def test_leaked_hotwords_segment_is_dropped(asr_settings):
    """靜音處吐出提示詞清單的段落被略過，正常段落保留，進度照樣推進到 100%。"""
    m = FakeWhisper([
        (0.0, 3.0, "我們今天討論 Kubernetes"),
        (3.0, 6.0, "人工智慧、Kubernetes"),  # 幻覺：把 hotwords 原樣吐出
        (6.0, 10.0, "好，散會"),
    ])
    segs, _, progress = run(m, hint_terms=["人工智慧", "Kubernetes"])
    assert [s["text"] for s in segs] == ["我們今天討論 Kubernetes", "好，散會"]
    assert progress[-1] == 1.0


def test_leaked_prompt_sentence_is_dropped(asr_settings, monkeypatch):
    """有設定提示句時，吐出提示句（或其片段重複）的段落被略過。"""
    monkeypatch.setattr(settings, "initial_prompt", "以下是國語與英文夾雜的會議錄音。")
    m = FakeWhisper([
        (0.0, 2.0, "開始開會"),
        (2.0, 5.0, "以下是國語與英文夾雜的會議錄音。"),
        (5.0, 8.0, "會議錄音，會議錄音。"),
    ])
    segs, _, _ = run(m)
    assert [s["text"] for s in segs] == ["開始開會"]


def test_leak_filter_keeps_real_speech_with_terms(asr_settings):
    """真的講到一個提示詞、或句子裡包含提示詞，不能被當成外洩。"""
    m = FakeWhisper([(0, 1, "Kubernetes"), (1, 2, "我們用 Jira 和 Kubernetes 管理")])
    segs, _, _ = run(m, hint_terms=["Kubernetes", "Jira"])
    assert [s["text"] for s in segs] == ["Kubernetes", "我們用 Jira 和 Kubernetes 管理"]


def test_full_hotwords_echo_with_prompt_is_dropped(asr_settings, monkeypatch):
    """有提示句又有字典提示詞時，Whisper 把整串 hotwords（提示句＋詞）原樣吐出來也要略過。"""
    monkeypatch.setattr(settings, "initial_prompt", "以下是會議錄音。")
    m = FakeWhisper([(0, 2, "開始開會"), (2, 5, "以下是會議錄音。 Kubernetes、Jira")])
    segs, _, _ = run(m, hint_terms=["Kubernetes", "Jira"])
    assert m.kwargs["hotwords"] == "以下是會議錄音。 Kubernetes、Jira"
    assert [s["text"] for s in segs] == ["開始開會"]


def test_empty_segments_are_dropped(asr_settings):
    m = FakeWhisper([(0, 1, "  "), (1, 2, "你好")])
    segs, _, _ = run(m)
    assert segs == [{"start": 1.0, "end": 2.0, "text": "你好"}]


def test_cancel_checked_per_segment(asr_settings):
    """轉錄過程每段都檢查取消。"""
    m = FakeWhisper([(0, 1, "一"), (1, 2, "二"), (2, 3, "三")])
    seen = []

    def check():
        seen.append(1)
        if len(seen) == 2:
            raise Canceled()

    with pytest.raises(Canceled):
        run(m, check_cancel=check)


# ---- 對齊語言（zh／en）分段 ----


@pytest.mark.parametrize("text, detected, expected", [
    ("大家好", "zh", "zh"),
    ("大家好", "en", "zh"),  # 視窗偵測成英文，但這段是中文字
    ("hello world", "zh", "en"),  # 視窗偵測成中文，但這段只有英文
    ("我們用 Kubernetes 部署", "en", "zh"),  # 中英夾雜以中文對齊
    ("日本語です", "ja", "ja"),
    ("今日係好天", "yue", "yue"),
    ("123 456", "en", "en"),  # 沒有文字可判斷時沿用偵測結果
    ("123", "", "zh"),
    ("こんにちは", "ja", "ja"),
])
def test_segment_language(text, detected, expected):
    assert segment_language(text, detected) == expected


def test_group_by_language_keeps_order_within_group():
    segs = [
        {"text": "大家好", "start": 0},
        {"text": "hello", "start": 1},
        {"text": "我是亞當", "start": 2},
        {"text": "nice to meet you", "start": 3},
    ]
    groups = group_by_language(segs, "zh")
    assert set(groups) == {"zh", "en"}
    assert [s["start"] for s in groups["zh"]] == [0, 2]
    assert [s["start"] for s in groups["en"]] == [1, 3]


def test_dropped_segments_reported_with_time(asr_settings, monkeypatch):
    """被過濾掉的段落交給 on_dropped（之後寫進警告），不是靜默丟掉。"""
    monkeypatch.setattr(settings, "initial_prompt", "以下是會議錄音。")
    m = FakeWhisper([(0, 2, "開始開會"), (2.5, 5, "以下是會議錄音。 Kubernetes、Jira")])
    dropped = []
    segs, _ = asr.transcribe(m, None, hint_terms=["Kubernetes", "Jira"], duration=10.0,
                             on_progress=lambda p: None, check_cancel=lambda: None, on_dropped=dropped.append)
    assert [s["text"] for s in segs] == ["開始開會"]
    assert dropped == [{"start": 2.5, "end": 5.0, "text": "以下是會議錄音。 Kubernetes、Jira"}]


def test_prompt_with_single_term_echo_is_dropped_but_real_speech_kept(asr_settings, monkeypatch):
    """提示句＋一個詞的外洩也過濾；只講到詞、或句子裡帶到詞的正常段落保留。"""
    monkeypatch.setattr(settings, "initial_prompt", "以下是會議錄音。")
    m = FakeWhisper([
        (0, 1, "以下是會議錄音 Kubernetes"),
        (1, 2, "Kubernetes"),
        (2, 3, "Kubernetes 很好用"),
        (3, 4, "我們用 Jira 和 Kubernetes 管理"),
    ])
    segs, _, _ = run(m, hint_terms=["Kubernetes", "Jira"])
    assert [s["text"] for s in segs] == ["Kubernetes", "Kubernetes 很好用", "我們用 Jira 和 Kubernetes 管理"]
