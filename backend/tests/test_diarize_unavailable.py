"""講者辨識無法使用（沒有 HF token、gated 模型未同意條款）時，仍輸出無講者的逐字稿並記錄原因。"""
from __future__ import annotations

import sys
import types

import pytest

from app import storage
from app.config import settings
from app.jobs import queue
from app.jobs.worker import Worker
from app.pipeline import align as align_mod
from app.pipeline import asr as asr_mod
from app.pipeline import diarize as diarize_mod
from app.pipeline.backend import GpuBackend
from app.pipeline.diarize import GATED_HINT, DiarizationUnavailable
from app.pipeline.models import ModelManager

from fakes import FakeBackend, patch_audio, zh


class GatedRepoError(Exception):
    """模擬 huggingface_hub 的 GatedRepoError（判斷靠類別名稱）。"""


def install_fake_pyannote(monkeypatch, from_pretrained):
    calls = []

    class Pipeline:
        @staticmethod
        def from_pretrained(name, token=None):
            calls.append((name, token))
            return from_pretrained()

    pkg = types.ModuleType("pyannote")
    mod = types.ModuleType("pyannote.audio")
    mod.Pipeline = Pipeline
    pkg.audio = mod
    monkeypatch.setitem(sys.modules, "pyannote", pkg)
    monkeypatch.setitem(sys.modules, "pyannote.audio", mod)
    return calls


def raise_(e):
    def f():
        raise e

    return f


# ---- load_pipeline 的錯誤分類 ----


def test_no_token_raises_unavailable_without_importing_pyannote(monkeypatch):
    """沒有 HF_TOKEN：直接回報原因，不嘗試下載（測試環境沒有裝 pyannote，也不會 ImportError）。"""
    monkeypatch.setattr(settings, "hf_token", "")
    monkeypatch.delitem(sys.modules, "pyannote.audio", raising=False)
    with pytest.raises(DiarizationUnavailable, match="HF_TOKEN"):
        diarize_mod.load_pipeline()


@pytest.mark.parametrize("err", [
    GatedRepoError("Access to model is restricted"),
    RuntimeError("401 Client Error: Unauthorized"),
    RuntimeError("403 Forbidden: you must accept the conditions"),
    RuntimeError("Cannot access gated repo"),
])
def test_gated_errors_become_unavailable_with_hint(monkeypatch, err):
    """gated／401／403：轉成 DiarizationUnavailable，訊息提示要設定 token 並同意條款。"""
    monkeypatch.setattr(settings, "hf_token", "hf_dummy")
    calls = install_fake_pyannote(monkeypatch, raise_(err))
    with pytest.raises(DiarizationUnavailable) as ei:
        diarize_mod.load_pipeline()
    assert str(ei.value) == GATED_HINT
    assert ei.value.__cause__ is err
    assert calls == [(settings.diarization_model, "hf_dummy")]


def test_pipeline_none_is_treated_as_gated(monkeypatch):
    """舊版 pyannote 在 gated 時回傳 None。"""
    monkeypatch.setattr(settings, "hf_token", "hf_dummy")
    install_fake_pyannote(monkeypatch, lambda: None)
    with pytest.raises(DiarizationUnavailable, match="同意"):
        diarize_mod.load_pipeline()


def test_other_load_errors_are_not_disguised_as_gated(monkeypatch):
    """網路逾時等非 gated 錯誤原樣往上丟，不誤報成 token 問題。"""
    monkeypatch.setattr(settings, "hf_token", "hf_dummy")
    err = ConnectionError("Read timed out")
    install_fake_pyannote(monkeypatch, raise_(err))
    with pytest.raises(ConnectionError):
        diarize_mod.load_pipeline()


# ---- 經過 GpuBackend + runner + worker ----


def make_job():
    job = queue.create(filename="a.m4a", model="large-v3", diarize=True, num_speakers=None,
                       dictionary_ids=[], audio_ext=".m4a", size_bytes=3)
    storage.job_dir(job["id"]).mkdir(parents=True, exist_ok=True)
    storage.original_path(job).write_bytes(b"abc")
    return job


@pytest.fixture()
def gpu_steps(monkeypatch):
    """GpuBackend 的轉錄與對齊換成假的，講者辨識走真的 load_pipeline。"""
    monkeypatch.setattr(asr_mod, "load_whisper", lambda opt: "W")
    monkeypatch.setattr(asr_mod, "transcribe", lambda m, audio, **kw: (
        [{"start": 0.0, "end": 2.0, "text": "大家好"}, {"start": 2.0, "end": 4.0, "text": "我是亚当"}], "zh"))
    monkeypatch.setattr(align_mod, "load_aligner", lambda lang: "A")
    monkeypatch.setattr(align_mod, "align", lambda a, segs, audio, lang: [{**s, "words": []} for s in segs])


def run_job(manager=None):
    job = make_job()
    Worker(GpuBackend(manager or ModelManager(idle_seconds=600))).process(queue.claim_next())
    return queue.get(job["id"])


def assert_transcript_without_speakers(job, reason):
    assert job["status"] == "done" and job["has_transcript"]
    assert any(reason in w for w in zh(job["warnings"]))  # 原因記在任務的警告
    original, _ = storage.load(job["id"])
    assert [s["text"] for s in original["segments"]] == ["大家好", "我是亞當"]
    assert all(s["speaker"] is None for s in original["segments"])
    assert original["speakers"] == {}
    d = original["diarization"]
    assert d["enabled"] is True and d["ok"] is False and reason in zh(d["error"])


def test_no_token_job_still_outputs_transcript(data_dir, monkeypatch, gpu_steps):
    patch_audio(monkeypatch)
    monkeypatch.setattr(settings, "hf_token", "")
    job = run_job()
    assert_transcript_without_speakers(job, "HF_TOKEN")


def test_gated_job_still_outputs_transcript(data_dir, monkeypatch, gpu_steps):
    patch_audio(monkeypatch)
    monkeypatch.setattr(settings, "hf_token", "hf_dummy")
    calls = install_fake_pyannote(monkeypatch, raise_(GatedRepoError("gated")))
    job = run_job()
    assert_transcript_without_speakers(job, GATED_HINT)
    assert len(calls) == 1  # 不是 OOM，不重試


def test_unexpected_diarization_error_still_outputs_transcript(data_dir, monkeypatch, gpu_steps):
    """其他講者辨識錯誤也不讓整個任務失敗，原因以「講者辨識失敗：」開頭記錄。"""
    patch_audio(monkeypatch)
    monkeypatch.setattr(settings, "hf_token", "hf_dummy")
    install_fake_pyannote(monkeypatch, raise_(ConnectionError("Read timed out")))
    job = run_job()
    assert_transcript_without_speakers(job, "講者辨識失敗：Read timed out")


def test_fake_backend_unavailable_recorded_on_job(data_dir, monkeypatch):
    """worker 把講者辨識失敗的原因存進任務的 warnings（API 會顯示）。"""
    patch_audio(monkeypatch)
    job = make_job()
    Worker(FakeBackend(diarize_error=DiarizationUnavailable("沒有設定 HF_TOKEN"))).process(queue.claim_next())
    got = queue.get(job["id"])
    assert got["status"] == "done" and got["warnings"] == ["沒有設定 HF_TOKEN"]
