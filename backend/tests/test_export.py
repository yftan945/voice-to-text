import json

from app import export


def sample():
    return {
        "language": "zh",
        "duration": 3700.0,
        "model": "large-v3",
        "speakers": {"SPEAKER_00": {"name": "Alex"}, "SPEAKER_01": {"name": None}},
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.2344, "speaker": "SPEAKER_00", "text": "大家好"},
            {"id": 1, "start": 1.5, "end": 3.0, "speaker": "SPEAKER_00", "text": "今天開會"},
            {"id": 2, "start": 3661.5, "end": 3662.25, "speaker": "SPEAKER_01", "text": "<好>"},
            {"id": 3, "start": 3663.0, "end": 3663.0, "speaker": None, "text": "  "},
        ],
    }


def test_time_formats():
    assert export.fmt_srt_time(0) == "00:00:00,000"
    assert export.fmt_srt_time(3661.5) == "01:01:01,500"
    assert export.fmt_srt_time(59.9996) == "00:01:00,000"  # 四捨五入進位
    assert export.fmt_srt_time(-1) == "00:00:00,000"
    assert export.fmt_vtt_time(3661.5) == "01:01:01.500"
    assert export.fmt_clock(3661.9) == "01:01:01"
    assert export.fmt_srt_time(36000 + 0.001) == "10:00:00,001"


def test_srt():
    out = export.to_srt(sample())
    blocks = out.strip().split("\n\n")
    assert len(blocks) == 3  # 空白段落略過
    assert blocks[0] == "1\n00:00:00,000 --> 00:00:01,234\nAlex：大家好"
    assert blocks[2] == "3\n01:01:01,500 --> 01:01:02,250\n講者 2：<好>"


def test_vtt():
    out = export.to_vtt(sample())
    assert out.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.234\n<v Alex>大家好")
    assert "<v 講者 2>&lt;好&gt;" in out


def test_zero_length_cue_gets_minimum_duration():
    t = {"speakers": {}, "segments": [{"id": 0, "start": 5.0, "end": 5.0, "speaker": None, "text": "嗯"}]}
    assert "00:00:05,000 --> 00:00:05,001" in export.to_srt(t)


def test_txt_with_and_without_timestamps():
    t = sample()
    with_ts = export.to_txt(t, timestamps=True)
    assert with_ts.splitlines()[0] == "[00:00:00] Alex：大家好"
    assert "[01:01:01] 講者 2：<好>" in with_ts
    plain = export.to_txt(t, timestamps=False)
    # 連續同講者合併成一段；兩段中文之間沒有句末標點時補「，」
    assert plain.split("\n\n")[0] == "Alex：大家好，今天開會"


def test_markdown_and_json():
    md = export.to_markdown(sample(), title="會議")
    assert md.startswith("# 會議\n")
    assert "**Alex** `[00:00:00]`" in md
    data = json.loads(export.to_json(sample()))
    assert data["segments"][0]["speaker_name"] == "Alex"
    assert data["segments"][2]["speaker_name"] == "講者 2"
    assert data["speakers"]["SPEAKER_00"]["name"] == "Alex"


def test_no_speaker_lines_have_no_prefix():
    t = {"speakers": {}, "segments": [{"id": 0, "start": 1, "end": 2, "speaker": None, "text": "hello"}]}
    assert export.to_srt(t).splitlines()[2] == "hello"
    assert export.to_txt(t).strip() == "[00:00:01] hello"


def test_paragraph_join_adds_space_between_english_words():
    t = {"speakers": {}, "segments": [
        {"id": 0, "start": 0, "end": 1, "speaker": "A", "text": "open"},
        {"id": 1, "start": 1, "end": 2, "speaker": "A", "text": "source"},
    ]}
    assert export.to_txt(t, timestamps=False).strip() == "A：open source"


def test_paragraph_join_after_english_sentence():
    t = {"speakers": {}, "segments": [
        {"id": 0, "start": 0, "end": 1, "speaker": "A", "text": "Thanks."},
        {"id": 1, "start": 1, "end": 2, "speaker": "A", "text": "Bye"},
        {"id": 2, "start": 2, "end": 3, "speaker": "A", "text": "好的。"},
        {"id": 3, "start": 3, "end": 4, "speaker": "A", "text": "OK"},
    ]}
    # 英文接中文補空格；句末全形標點後接英文不補
    assert export.to_txt(t, timestamps=False).strip() == "A：Thanks. Bye 好的。OK"


# ---- B8：字幕內文清理 ----


def test_srt_vtt_collapse_blank_lines_and_arrow():
    t = {"speakers": {"A": {"name": "X-->Y"}}, "segments": [
        {"id": 0, "start": 0, "end": 1, "speaker": "A", "text": "第一行\n\n\n第二行 --> 第三行\r\n\r\n尾"},
    ]}
    srt = export.to_srt(t)
    assert srt == "1\n00:00:00,000 --> 00:00:01,000\nX→Y：第一行\n第二行 → 第三行\n尾\n"
    vtt = export.to_vtt(t)
    assert "\n\n第二行" not in vtt and vtt.count("-->") == 1
    assert "<v X→Y>第一行\n第二行 → 第三行\n尾" in vtt


# ---- F14：純文字段落：中文之間補逗號、停頓超過 2 秒換行 ----


def test_plain_join_adds_comma_between_chinese_and_breaks_on_gap():
    t = {"speakers": {}, "segments": [
        {"id": 0, "start": 0, "end": 1, "speaker": "A", "text": "大家好"},
        {"id": 1, "start": 1.2, "end": 2, "speaker": "A", "text": "今天開會"},
        {"id": 2, "start": 2.1, "end": 3, "speaker": "A", "text": "好的。"},
        {"id": 3, "start": 3.1, "end": 4, "speaker": "A", "text": "第一點"},
        {"id": 4, "start": 6.5, "end": 7, "speaker": "A", "text": "第二點"},  # 停頓 2.5 秒
        {"id": 5, "start": 7.1, "end": 8, "speaker": "B", "text": "收到"},
    ]}
    plain = export.to_txt(t, timestamps=False)
    assert plain == "A：大家好，今天開會，好的。第一點\n第二點\n\nB：收到\n"
