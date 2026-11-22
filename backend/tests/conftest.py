from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """每個測試用獨立的資料目錄與 SQLite。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "static_dir", tmp_path / "static")
    # 不受執行環境影響：模擬 Breeze 尚未轉檔
    monkeypatch.setattr(settings, "breeze_model_path", str(tmp_path / "no-breeze"))
    db.init_db()
    return settings.data_dir
