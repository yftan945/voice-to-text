import pytest

from app import dictionary as dict_mod
from app.config import settings
from app.pipeline import runner
from app.pipeline.errors import GPU_BUSY_MESSAGE, Canceled, GpuBusy

from fakes import DiarizationUnavailable, FakeBackend, patch_audio


def make_input(tmp_path, **kw):
    src = tmp_path / "in.qta"
    src.write_bytes(b"x")
    base = dict(
        job_id="job1", source=src, playback=tmp_path / "playback.m4a",
        model=settings.model_option("large-v3"), diarize=True, num_speakers=None,
        dictionary=dict_mod.parse("人工智慧 = 人工只能\nAlex"), voiceprints=[],
    )
    base.update(kw)
    return runner.JobInput(**base)


def run(inp, backend):
    progress = []
    out = runner.run(inp, backend, lambda k, label, p: progress.append((k, label, p)), lambda: None)
    return out, progress


def test_full_pipeline_with_fake_backend(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    backend = FakeBackend()
    out, progress = run(make_input(tmp_path), backend)
    t = out.transcript
    # 提示詞來自字典
    assert backend.calls[0] == ("transcribe", "large-v3", ["人工智慧", "Alex"])
    # 第一段在 1.3 秒換講者（SPEAKER_02 講超過 1.5 秒，不會被當成短片段吸收） → 切成兩段；講者依出現順序重新編號
    assert [(s["speaker"], s["text"]) for s in t["segments"]] == [
        ("SPEAKER_00", "大家好"),
        ("SPEAKER_01", "我是亞當"),
        ("SPEAKER_00", "人工智慧很好"),  # 簡轉繁＋字典替換
    ]
    assert set(t["speakers"]) == {"SPEAKER_00", "SPEAKER_01"}
    assert t["speaker_embeddings"]["SPEAKER_00"] == [1.0, 0.0, 0.0]
    assert t["diarization"]["ok"] is True and t["warnings"] == []
    assert out.playback_created and out.duration == 4.0
    # 進度單調遞增並到 100%
    values = [p for _, _, p in progress]
    assert values == sorted(values) and values[-1] == 1.0
    assert not (settings.work_dir / "job1.wav").exists()  # 暫存 WAV 會清掉


def test_voiceprint_match_sets_name(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    vps = [{"id": 7, "name": "亞當", "embedding": [0.1, 0.99, 0.0]},
           {"id": 8, "name": "陌生人", "embedding": [0.0, 0.0, 1.0]}]
    out, _ = run(make_input(tmp_path, voiceprints=vps), FakeBackend())
    spk = out.transcript["speakers"]
    assert spk["SPEAKER_01"]["name"] == "亞當" and spk["SPEAKER_01"]["voiceprint_id"] == 7
    assert spk["SPEAKER_00"]["name"] is None  # 信心不足維持 SPEAKER_xx


def test_diarization_unavailable_still_outputs_transcript(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    backend = FakeBackend(diarize_error=DiarizationUnavailable("pyannote 模型無法下載：gated"))
    out, _ = run(make_input(tmp_path), backend)
    t = out.transcript
    assert all(s["speaker"] is None for s in t["segments"])
    assert t["diarization"]["ok"] is False
    assert "gated" in t["diarization"]["error"]
    assert t["warnings"] and t["speakers"] == {}


def test_diarize_disabled_and_num_speakers(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    backend = FakeBackend()
    out, _ = run(make_input(tmp_path, diarize=False), backend)
    assert not any(c[0] == "diarize" for c in backend.calls)
    assert out.transcript["diarization"]["enabled"] is False
    backend = FakeBackend()
    run(make_input(tmp_path, num_speakers=3), backend)
    assert ("diarize", 3) in backend.calls


def test_align_failure_falls_back(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    out, _ = run(make_input(tmp_path), FakeBackend(align_error=RuntimeError("no model")))
    assert any("逐字對齊失敗" in w for w in out.transcript["warnings"])
    assert out.transcript["segments"]


@pytest.mark.parametrize("err", [
    RuntimeError("CUDA failed with error out of memory"),
    type("OutOfMemoryError", (RuntimeError,), {})("CUDA OOM"),
])
def test_oom_becomes_gpu_busy(data_dir, tmp_path, monkeypatch, err):
    patch_audio(monkeypatch)
    backend = FakeBackend(transcribe_error=err)
    with pytest.raises(GpuBusy) as ei:
        run(make_input(tmp_path), backend)
    assert str(ei.value) == GPU_BUSY_MESSAGE
    assert backend.unloaded


def test_oom_during_diarization_is_not_swallowed(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)
    with pytest.raises(GpuBusy):
        run(make_input(tmp_path), FakeBackend(diarize_error=RuntimeError("CUDA out of memory")))


def test_mixed_language_alignment_groups(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)

    class MixedBackend(FakeBackend):
        def transcribe(self, *a, **k):
            return [
                {"start": 0.0, "end": 2.0, "text": "大家好"},
                {"start": 2.5, "end": 4.0, "text": "hello world"},
            ], "zh"

        def align(self, segments, audio, language):
            self.calls.append(("align", language, [s["text"] for s in segments]))
            return super().align(segments, audio, language)

    backend = MixedBackend()
    out, _ = run(make_input(tmp_path, diarize=False), backend)
    aligns = [c for c in backend.calls if c[0] == "align"]
    assert ("align", "zh", ["大家好"]) in aligns and ("align", "en", ["hello world"]) in aligns
    assert [s["text"] for s in out.transcript["segments"]] == ["大家好", "hello world"]


def test_cancel(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)

    def cancel():
        raise Canceled()

    with pytest.raises(Canceled):
        runner.run(make_input(tmp_path), FakeBackend(), lambda *a: None, cancel)


def test_retry_after_oom_unloads_idle_models():
    from app.pipeline.backend import retry_after_oom
    from app.pipeline.models import ModelManager

    mgr = ModelManager(idle_seconds=600)
    with mgr.use("other", lambda: object()):
        pass
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("CUDA failed with error out of memory")
        return "ok"

    assert retry_after_oom(mgr, fn) == "ok"
    assert mgr.loaded() == [] and len(calls) == 2

    def always_oom():
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError):
        retry_after_oom(mgr, always_oom)


def test_model_manager_idle_unload():
    from app.pipeline.models import ModelManager

    mgr = ModelManager(idle_seconds=10)
    with mgr.use("a", lambda: "A") as a:
        assert a == "A"
        assert mgr.unload_idle(now=1e12) == []  # 使用中不卸載
    assert mgr.loaded() == ["a"]
    assert mgr.unload_idle(now=1e12) == ["a"]
    assert mgr.loaded() == []


# ---- B2：OOM 重試在 except 區塊外執行，保留目前這顆模型 ----


def test_retry_after_oom_runs_outside_except_and_keeps_current_model():
    import sys

    from app.pipeline.backend import retry_after_oom
    from app.pipeline.models import ModelManager

    mgr = ModelManager(idle_seconds=600)
    for key in ("current", "other"):
        with mgr.use(key, lambda: object()):
            pass
    seen = []

    def fn():
        seen.append((sys.exc_info()[0], mgr.loaded()))
        if len(seen) == 1:
            raise RuntimeError("CUDA failed with error out of memory")
        return "ok"

    assert retry_after_oom(mgr, fn, keep="current") == "ok"
    assert seen[0] == (None, ["current", "other"])
    # 重試時已離開 except（沒有正在處理的例外），而且只卸載了其他模型
    assert seen[1] == (None, ["current"])


def test_runner_unloads_outside_except_on_oom(data_dir, tmp_path, monkeypatch):
    import sys

    patch_audio(monkeypatch)
    seen = []

    class Backend(FakeBackend):
        def unload_all(self):
            seen.append(sys.exc_info()[0])

    with pytest.raises(GpuBusy) as ei:
        run(make_input(tmp_path), Backend(transcribe_error=RuntimeError("CUDA out of memory")))
    assert seen == [None]
    assert ei.value.__cause__ is None  # 不串接原本的例外，避免 traceback 抓著模型
    assert not (settings.work_dir / "job1.wav").exists()


# ---- B3：被當成提示詞幻覺略過的段落寫進警告 ----


def test_dropped_prompt_leak_segments_become_warnings(data_dir, tmp_path, monkeypatch):
    patch_audio(monkeypatch)

    class LeakyBackend(FakeBackend):
        def transcribe(self, audio, model, hint_terms, duration, on_progress, check_cancel, on_dropped=None):
            on_dropped({"start": 3725.4, "end": 3727.0, "text": "人工智慧、Alex"})
            return super().transcribe(audio, model, hint_terms, duration, on_progress, check_cancel)

    out, _ = run(make_input(tmp_path), LeakyBackend())
    w = out.transcript["warnings"]
    assert len(w) == 1 and "[01:02:05]" in w[0] and "人工智慧、Alex" in w[0] and "略過 1 段" in w[0]


def test_dropped_warning_is_truncated():
    items = [{"start": i, "end": i + 1, "text": "字" * 50} for i in range(7)]
    msg = runner.dropped_warning(items)
    assert msg.startswith("略過 7 段") and "另有 2 段" in msg and "…" in msg
