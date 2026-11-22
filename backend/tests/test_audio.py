"""ffmpeg 音軌選擇：從 0:a:0 依序嘗試，選第一條能解碼的（mock subprocess，不需要 ffmpeg）。"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from app import dictionary as dict_mod
from app.config import settings
from app.pipeline import audio as audio_mod
from app.pipeline import runner

from fakes import FakeBackend


class FakeFfmpeg:
    """依音軌編號決定 ffmpeg 結果：ok＝寫出完整 WAV、header＝只寫出 44 bytes 標頭、fail＝回傳錯誤。"""

    def __init__(self, behavior, streams=2):
        self.behavior = behavior
        self.streams = streams
        self.maps: list[str] = []
        self.commands: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.commands.append(cmd)
        if cmd[0] == "ffprobe":
            data = {"streams": [{"index": i} for i in range(self.streams)], "format": {"duration": "12.5"}}
            return subprocess.CompletedProcess(cmd, 0, json.dumps(data), "")
        m = cmd[cmd.index("-map") + 1]
        self.maps.append(m)
        idx = int(m.rsplit(":", 1)[1])
        dst = cmd[-1]
        how = self.behavior.get(idx, "fail")
        if how == "ok":
            with open(dst, "wb") as fh:
                fh.write(b"\0" * 1000)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if how == "header":
            with open(dst, "wb") as fh:
                fh.write(b"\0" * 44)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        # 失敗時留下一些殘檔，確認不會被當成成功
        with open(dst, "wb") as fh:
            fh.write(b"\0" * 1000)
        return subprocess.CompletedProcess(cmd, 1, "", f"Decoder (codec apac) not found for stream #0:{idx}")


@pytest.fixture()
def ffmpeg(monkeypatch):
    def install(behavior, streams=2):
        fake = FakeFfmpeg(behavior, streams)
        monkeypatch.setattr(audio_mod, "subprocess",
                            SimpleNamespace(run=fake, CompletedProcess=subprocess.CompletedProcess))
        return fake

    return install


def test_first_stream_undecodable_falls_back_to_second(ffmpeg, tmp_path):
    """iPhone .qta：第一條（APAC）無法解碼時，改用第二條。"""
    fake = ffmpeg({0: "fail", 1: "ok"})
    idx = audio_mod.convert_to_wav(tmp_path / "in.qta", tmp_path / "out.wav", 2)
    assert idx == 1
    assert fake.maps == ["0:a:0", "0:a:1"]


def test_first_decodable_stream_wins(ffmpeg, tmp_path):
    """第一條能解碼就用第一條，不再嘗試後面的音軌（不會挑聲道多的那條）。"""
    fake = ffmpeg({0: "ok", 1: "ok"})
    assert audio_mod.convert_to_wav(tmp_path / "in.qta", tmp_path / "out.wav", 2) == 0
    assert fake.maps == ["0:a:0"]


def test_always_maps_stream_explicitly_with_16k_mono(ffmpeg, tmp_path):
    """一定用 -map 明確選音軌，並轉成 16kHz 單聲道 PCM。"""
    fake = ffmpeg({0: "ok"}, streams=1)
    audio_mod.convert_to_wav(tmp_path / "in.m4a", tmp_path / "out.wav", 1)
    cmd = fake.commands[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-map") + 1] == "0:a:0"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"


def test_header_only_output_is_treated_as_failure(ffmpeg, tmp_path):
    """ffmpeg 回傳成功但只寫出 WAV 標頭（沒有音訊）也算失敗，繼續試下一條。"""
    fake = ffmpeg({0: "header", 1: "ok"})
    assert audio_mod.convert_to_wav(tmp_path / "in.qta", tmp_path / "out.wav", 2) == 1
    assert fake.maps == ["0:a:0", "0:a:1"]


def test_tries_streams_in_order_until_one_works(ffmpeg, tmp_path):
    fake = ffmpeg({0: "fail", 1: "fail", 2: "ok", 3: "ok"}, streams=4)
    assert audio_mod.convert_to_wav(tmp_path / "in.mkv", tmp_path / "out.wav", 4) == 2
    assert fake.maps == ["0:a:0", "0:a:1", "0:a:2"]


def test_all_streams_fail_raises_with_last_error(ffmpeg, tmp_path):
    fake = ffmpeg({}, streams=2)
    with pytest.raises(audio_mod.AudioError) as ei:
        audio_mod.convert_to_wav(tmp_path / "in.qta", tmp_path / "out.wav", 2)
    assert fake.maps == ["0:a:0", "0:a:1"]
    assert "所有音軌都無法解碼" in str(ei.value) and "#0:1" in str(ei.value)


def test_no_audio_stream_raises_without_running_ffmpeg(ffmpeg, tmp_path):
    fake = ffmpeg({0: "ok"})
    with pytest.raises(audio_mod.AudioError):
        audio_mod.convert_to_wav(tmp_path / "in.mp4", tmp_path / "out.wav", 0)
    assert fake.commands == []


def test_stream_count_from_ffprobe_when_not_given(ffmpeg, tmp_path):
    """沒傳音軌數時先用 ffprobe 查，再依序嘗試。"""
    fake = ffmpeg({0: "fail", 1: "ok"}, streams=2)
    assert audio_mod.convert_to_wav(tmp_path / "in.qta", tmp_path / "out.wav") == 1
    assert fake.commands[0][0] == "ffprobe"
    assert fake.maps == ["0:a:0", "0:a:1"]


def test_missing_ffmpeg_raises_audio_error(monkeypatch, tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(audio_mod, "subprocess", SimpleNamespace(run=run))
    with pytest.raises(audio_mod.AudioError, match="找不到 ffmpeg"):
        audio_mod.convert_to_wav(tmp_path / "in.qta", tmp_path / "out.wav", 1)


def test_probe_counts_streams_and_falls_back_to_stream_duration(monkeypatch, tmp_path):
    data = {"streams": [{"duration": "N/A"}, {"duration": "7.25"}], "format": {"duration": "N/A"}}

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(data), "")

    monkeypatch.setattr(audio_mod, "subprocess", SimpleNamespace(run=run))
    info = audio_mod.probe(tmp_path / "in.qta")
    assert info.audio_streams == 2 and info.duration == 7.25


def test_make_playback_uses_selected_stream(ffmpeg, tmp_path):
    """播放用音檔要用同一條可解碼的音軌。"""
    fake = ffmpeg({1: "ok"})
    audio_mod.make_playback(tmp_path / "in.qta", tmp_path / "playback.m4a", 1)
    assert fake.maps == ["0:a:1"]
    assert (tmp_path / "playback.m4a").exists()


def test_runner_passes_chosen_stream_to_playback(data_dir, tmp_path, monkeypatch):
    """runner 把轉檔選到的音軌編號傳給播放檔，避免播放檔用到無法解碼的那條。"""
    import numpy as np

    used = []
    monkeypatch.setattr(audio_mod, "probe", lambda p: audio_mod.ProbeResult(duration=4.0, audio_streams=2))

    def convert(src, dst, n=None):
        dst.write_bytes(b"RIFF")
        return 1

    monkeypatch.setattr(audio_mod, "convert_to_wav", convert)
    monkeypatch.setattr(audio_mod, "make_playback", lambda src, dst, idx: used.append(idx))
    monkeypatch.setattr(audio_mod, "load_wav", lambda p: np.zeros(4 * audio_mod.SAMPLE_RATE, dtype=np.float32))
    src = tmp_path / "in.qta"
    src.write_bytes(b"x")
    inp = runner.JobInput(job_id="j", source=src, playback=tmp_path / "pb.m4a",
                          model=settings.model_option("large-v3"), diarize=False, num_speakers=None,
                          dictionary=dict_mod.parse(""))
    runner.run(inp, FakeBackend(), lambda *a: None, lambda: None)
    assert used == [1]


# ---- B10：只允許讀本機檔案，拒絕會連到外部的格式 ----


def test_ffmpeg_and_ffprobe_restrict_protocols(ffmpeg, tmp_path):
    fake = ffmpeg({0: "ok"}, streams=1)
    audio_mod.convert_to_wav(tmp_path / "in.m4a", tmp_path / "out.wav")
    audio_mod.make_playback(tmp_path / "in.m4a", tmp_path / "pb.m4a", 0)
    assert [c[0] for c in fake.commands] == ["ffprobe", "ffmpeg", "ffmpeg"]
    for cmd in fake.commands:
        i = cmd.index("-protocol_whitelist")
        assert cmd[i + 1] == "file,pipe"
    for cmd in fake.commands[1:]:
        assert cmd.index("-protocol_whitelist") < cmd.index("-i")  # 是輸入選項，要放在 -i 前面


@pytest.mark.parametrize("fmt", ["hls", "concat", "dash", "mov,mp4,m4a,3gp,3g2,mj2,hls"])
def test_probe_rejects_playlist_formats(monkeypatch, tmp_path, fmt):
    data = {"streams": [{"index": 0}], "format": {"format_name": fmt, "duration": "5"}}
    monkeypatch.setattr(audio_mod, "subprocess", SimpleNamespace(
        run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(data), "")))
    with pytest.raises(audio_mod.AudioError, match="不支援這種檔案格式"):
        audio_mod.probe(tmp_path / "in.m3u8")


def test_probe_accepts_normal_audio(monkeypatch, tmp_path):
    data = {"streams": [{"index": 0}], "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "5"}}
    monkeypatch.setattr(audio_mod, "subprocess", SimpleNamespace(
        run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(data), "")))
    info = audio_mod.probe(tmp_path / "in.qta")
    assert info.format_name.startswith("mov") and info.audio_streams == 1
