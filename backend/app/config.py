"""設定值：全部從環境變數讀取，方便在 docker compose 裡調整。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class ModelOption:
    """一個可選的辨識模型。path 可以是 faster-whisper 內建名稱、HF repo 或本機 CTranslate2 目錄。"""

    id: str
    label: str
    path: str
    description: str = ""
    local_dir: bool = False  # True 表示 path 是本機目錄，不存在時停用

    def available(self) -> bool:
        if not self.local_dir:
            return True
        return (Path(self.path) / "model.bin").is_file()


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "/data")))
    models_dir: Path = field(default_factory=lambda: Path(_env("MODELS_DIR", "/models")))
    static_dir: Path = field(default_factory=lambda: Path(_env("STATIC_DIR", "/app/static")))
    hf_token: str = field(default_factory=lambda: _env("HF_TOKEN", ""))

    # 預設模型：benchmark 結果 Breeze-ASR-25 在台灣國語與中英夾雜最準（2026-11-18）
    default_model: str = field(default_factory=lambda: _env("DEFAULT_MODEL", "breeze-asr-25"))
    breeze_model_path: str = field(
        default_factory=lambda: _env("BREEZE_MODEL_PATH", "/models/ct2/breeze-asr-25-f16")
    )
    # 空字串＝自動偵測；搭配 multilingual 每個 30 秒視窗各自偵測，英文段落不會被翻成中文
    language: str = field(default_factory=lambda: _env("ASR_LANGUAGE", ""))
    multilingual: bool = field(
        default_factory=lambda: _env("ASR_MULTILINGUAL", "1").lower() in ("1", "true", "yes")
    )
    initial_prompt: str = field(
        # 預設不放提示句：實測 Whisper 會在靜音處把提示句原樣吐出來（幻覺）；繁體由 OpenCC 負責
        default_factory=lambda: _env("ASR_INITIAL_PROMPT", "")
    )
    beam_size: int = field(default_factory=lambda: _env_int("ASR_BEAM_SIZE", 5))
    compute_type: str = field(default_factory=lambda: _env("ASR_COMPUTE_TYPE", "float16"))

    diarization_model: str = field(
        default_factory=lambda: _env("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")
    )
    embedding_model: str = field(
        default_factory=lambda: _env("EMBEDDING_MODEL", "pyannote/wespeaker-voxceleb-resnet34-LM")
    )
    voiceprint_threshold: float = field(
        default_factory=lambda: _env_float("VOICEPRINT_THRESHOLD", 0.55)
    )
    idle_unload_seconds: int = field(default_factory=lambda: _env_int("IDLE_UNLOAD_SECONDS", 600))
    max_upload_mb: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_MB", 2048))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "voice-to-text.sqlite3"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    def model_options(self) -> list[ModelOption]:
        # 顯示順序＝上傳頁的選項順序（預設的 Breeze 放第一個）
        return [
            ModelOption(
                id="breeze-asr-25",
                label="精準（Breeze-ASR-25）",
                path=self.breeze_model_path,
                description="最適合台灣國語與中英夾雜，預設使用",
                local_dir=True,
            ),
            ModelOption(
                id="large-v3-turbo",
                label="快速（Whisper large-v3-turbo）",
                path="large-v3-turbo",
                description="速度約快 2.5 倍，同音錯字較多",
            ),
            ModelOption(
                id="large-v3",
                label="多語（Whisper large-v3）",
                path="large-v3",
                description="適合英文或其他語言為主的錄音",
            ),
        ]

    def model_option(self, model_id: str) -> ModelOption | None:
        for opt in self.model_options():
            if opt.id == model_id:
                return opt
        return None

    def effective_default_model(self) -> str:
        """實際使用的預設模型：設定的預設模型沒安裝（例如 Breeze 還沒轉成 CTranslate2）時，
        改用 large-v3，再不行用第一個可用的模型。"""
        opt = self.model_option(self.default_model)
        if opt is not None and opt.available():
            return opt.id
        fallback = self.model_option("large-v3")
        if fallback is not None and fallback.available():
            return fallback.id
        for o in self.model_options():
            if o.available():
                return o.id
        return self.default_model

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.audio_dir, self.work_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
