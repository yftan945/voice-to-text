#!/usr/bin/env python3
"""示範資料：在指定的資料目錄建立 SQLite 與檔案，塞入「虛構」的示範任務、字典與聲紋，供 README 截圖用。

寫入方式與正式程式完全相同（走 app.db／jobs.queue／storage／dictionary_store／voiceprint），
不需要 GPU，也不會呼叫任何模型。內容全部是虛構的（講者、產品、對話都不對應真實人物或公司）。

用法（通常由 scripts/demo.sh 呼叫）：
  python3 scripts/demo_seed.py --data-dir /tmp/demo-data --audio-cache /tmp/demo-audio
  python3 scripts/demo_seed.py --audio-only --audio-cache /tmp/demo-audio   # 只產生音檔（需要 ffmpeg，不載入 app）

音檔：用 ffmpeg 產生同長度、低音量的 m4a（AAC），讓播放器可以載入；已存在於 --audio-cache 的檔案直接沿用。
資料目錄裡已經有任務時拒絕寫入，避免汙染真實資料。
"""
from __future__ import annotations

import argparse
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---- 固定的任務 id（網址穩定，截圖／文件可以直接引用） ----
JOB_MEETING = "5e8d1c0a4b7f4c1e9a3d2b6f7c8e9a01"
JOB_INTERVIEW = "5e8d1c0a4b7f4c1e9a3d2b6f7c8e9a02"
JOB_MEMO = "5e8d1c0a4b7f4c1e9a3d2b6f7c8e9a03"
JOB_DESIGN = "5e8d1c0a4b7f4c1e9a3d2b6f7c8e9a04"
JOB_RUNNING = "5e8d1c0a4b7f4c1e9a3d2b6f7c8e9a05"
JOB_QUEUED_1 = "5e8d1c0a4b7f4c1e9a3d2b6f7c8e9a06"
JOB_QUEUED_2 = "5e8d1c0a4b7f4c1e9a3d2b6f7c8e9a07"

# ---- 逐字稿內容（講者索引 0／1／2，對應 SPEAKER_00／01／02） ----

MEETING = [
    (0, "大家早安，我們開始這週的產品週會，先快速對一下這個 sprint 的進度。"),
    (0, "今天有三個重點：onboarding 流程的改版、新的 API 整合，還有下週的 release 時間表。"),
    (1, "好，我先說 onboarding。新版的設計稿昨天已經交給 Leo 了，主要是把原本五個步驟縮成三個。"),
    (1, "使用者測試的 feedback 蠻明確的，大家都覺得一開始要填的欄位太多，所以我們改成先進到首頁，個人資料之後再補。"),
    (0, "聽起來合理。那這樣對轉換率的預期大概是多少？"),
    (1, "內部估計 completion rate 可以從現在的六成多提升到八成左右，不過還是要等 A/B test 的數據出來才準。"),
    (2, "工程這邊我補充一下，onboarding 的前端大概三天可以做完，但有一個地方要注意，登入之後的 redirect 邏輯要重寫。"),
    (0, "那會不會影響到現有使用者？"),
    (2, "不會，舊的使用者會直接跳過 onboarding，我們用 feature flag 控制，有問題可以馬上關掉。"),
    (0, "很好。接下來談 API 整合，Leo 目前卡在哪裡？"),
    (2, "第三方的 API 文件有點舊，有兩個 endpoint 的回傳格式跟文件不一樣，我已經寫信去問對方的技術窗口了。"),
    (2, "如果週四之前沒回覆，我就先自己寫一層 adapter，把格式差異吸收掉，這樣不會 block 到其他功能。"),
    (0, "這個做法我同意，先不要被外部的時程綁住。需要我幫忙跟對方的產品經理催一下嗎？"),
    (2, "可以，麻煩你今天下午先寄一封 follow-up，我把技術細節整理給你。"),
    (0, "沒問題，我會在下班前寄出去。再來是 dashboard 的部分，Mia 這邊有更新嗎？"),
    (1, "有，dashboard 的第二版 wireframe 已經完成，重點是把最常用的三個圖表放在最上面，其他的收進可以展開的區塊。"),
    (1, "我想請 Leo 評估一下，圖表要即時更新的話，後端的負擔會不會太大？"),
    (2, "如果是每分鐘更新一次，應該沒問題；要做到 real-time 的話，就要改用 WebSocket，那工時會多一個 sprint。"),
    (0, "那我們先用每分鐘更新，real-time 放進 backlog，之後看使用者的需求再決定。"),
    (1, "好，我會在設計稿上標註這個限制，避免開發的時候搞混。"),
    (0, "接著是 QA 的部分，這個 sprint 有幾個 bug 還沒關，Leo 可以簡單報告一下嗎？"),
    (2, "目前還有四個 open 的 bug，其中兩個是 P1：推播通知在 Android 上偶爾收不到，還有匯出功能遇到特殊字元會失敗。"),
    (2, "這兩個我預計明天中午前修完，剩下兩個 P2 可以排到下個 sprint。"),
    (0, "好，那 release 的時間我們暫定下週三，如果 P1 的 bug 沒修完就延到週五，大家有意見嗎？"),
    (1, "我這邊沒問題，release note 的文案我週二前會準備好。"),
    (2, "週三可以，不過我建議週二晚上先做一次 staging 的完整測試。"),
    (0, "好，那就這麼決定。總結一下：onboarding 三天、API 週四看回覆、dashboard 先每分鐘更新、週三 release。謝謝大家，今天到這裡。"),
]

INTERVIEW = [
    (0, "謝謝你今天撥時間跟我們聊，先請你簡單介紹一下平常是怎麼使用這個 App 的。"),
    (1, "我大部分是在通勤的時候用，會議錄音上傳之後，回到公司就可以直接看逐字稿。"),
    (0, "那有沒有遇到什麼不方便的地方？"),
    (1, "有，像是專有名詞常常被聽錯，例如 sprint 會變成別的字，每次都要手動改。"),
    (0, "如果可以先建立一份字典，把常用的詞放進去，你覺得會有幫助嗎？"),
    (1, "會，這樣就不用每次都改，而且團隊裡其他人也可以共用同一份。"),
    (0, "了解。另外匯出的格式，你比較常用哪一種？"),
    (1, "我比較常用 Word，因為要貼進會議紀錄；字幕檔 SRT 偶爾會用在內部教學影片。"),
    (0, "好的，這些回饋對我們很有幫助，我們會整理後排進 backlog。"),
    (1, "沒問題，之後有新功能也歡迎再找我測試。"),
]

MEMO = [
    (0, "備忘一下，這週五之前要完成發版前的檢查清單。"),
    (0, "第一，確認 staging 環境的資料庫已經備份，而且備份檔可以還原。"),
    (0, "第二，把 API 文件更新到最新版，特別是新增的兩個 endpoint。"),
    (0, "第三，onboarding 的 feature flag 預設要關閉，等 QA 確認之後再打開。"),
    (0, "最後，release note 要請 Mia 幫忙看過文字，週四中午前交給大家。"),
    (0, "就這樣，記得週四早上再提醒自己一次。"),
]

DESIGN = [
    (1, "這是首頁新版的 layout，我把最近的任務放在最上面，其他的操作收到側邊欄。"),
    (2, "側邊欄在小螢幕上會不會太擠？我擔心手機上點不到。"),
    (1, "我有考慮，所以手機版會改成底部的 tab bar，圖示加上文字標籤。"),
    (2, "那就沒問題，實作上我們可以共用同一組 component，只是排版不一樣。"),
    (1, "另外深色模式的配色我也調整過了，對比度符合無障礙的建議。"),
    (2, "好，我今天先把 design token 整理成 JSON，之後前端直接引用就不會有色差。"),
    (1, "太好了，我晚點把最新的 Figma 連結貼到頻道裡。"),
    (2, "收到，下個 sprint 開始前我們再對一次細節。"),
    (1, "沒問題，那今天的設計評審就先到這裡。"),
]


# ---- 逐字稿時間軸（依字數估算每段長度，段與段之間留停頓） ----

_ASCII_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9/\-]*")
_CJK = re.compile(r"[⺀-鿿]")


def _seg_seconds(text: str) -> float:
    words = len(_ASCII_WORD.findall(text))
    cjk = len(_CJK.findall(text))
    pauses = len(re.findall(r"[，、；：,;:]", text))
    return 0.6 + cjk * 0.165 + words * 0.4 + pauses * 0.18


def layout(lines: list[tuple[int, str]]) -> tuple[list[dict], float]:
    """回傳（segments, 總長度秒數）。時間戳固定、不隨機，每次建出來都一樣。"""
    segments: list[dict] = []
    cursor = 1.1
    prev = None
    for i, (spk, text) in enumerate(lines):
        start = round(cursor, 3)
        end = round(cursor + _seg_seconds(text), 3)
        segments.append({"id": i, "start": start, "end": end, "speaker": f"SPEAKER_{spk:02d}", "text": text})
        gap = 0.3 if prev == spk else 0.55 + 0.12 * ((i * 7) % 5)
        cursor = end + gap
        prev = spk
    return segments, math.ceil(cursor + 1.0)


def make_embedding(seed: int, dim: int = 256) -> list[float]:
    rng = random.Random(seed)
    v = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in v))
    return [round(x / norm, 5) for x in v]


# 聲紋 seed（同一個 seed ＝ 同一個人）：講者的 embedding 與聲紋庫的向量一致，結果頁會顯示「已比對聲紋」
VP_SEED = {"設計師 Mia": 101, "工程師 Leo": 202}
HOST_SEED = 303  # 主持人：沒有存成聲紋（結果頁會出現「存成聲紋」）

# 任務清單：done 的任務 → 逐字稿、音檔長度由 layout 決定
# speakers：講者索引 → (名稱或 None, 聲紋 seed 或 None, 相似度)
DONE_JOBS = [
    {
        "id": JOB_MEETING, "filename": "產品週會_第12週.m4a", "lines": MEETING, "model": "breeze-asr-25",
        "num_speakers": 3, "dictionary_ids": [1], "age_hours": 3.0, "process_ratio": 0.062,
        "speakers": {0: ("主持人", None, None), 1: ("設計師 Mia", 101, 0.83), 2: ("工程師 Leo", 202, 0.78)},
    },
    {
        "id": JOB_DESIGN, "filename": "設計評審_新版首頁.m4a", "lines": DESIGN, "model": "breeze-asr-25",
        "num_speakers": None, "dictionary_ids": [1], "age_hours": 26.0, "process_ratio": 0.065,
        "speakers": {1: ("設計師 Mia", 101, 0.81), 2: ("工程師 Leo", 202, 0.76)},
    },
    {
        "id": JOB_INTERVIEW, "filename": "客戶訪談_使用情境.m4a", "lines": INTERVIEW, "model": "large-v3-turbo",
        "num_speakers": 2, "dictionary_ids": [], "age_hours": 52.0, "process_ratio": 0.027,
        "speakers": {0: (None, None, None), 1: (None, None, None)},
    },
    {
        "id": JOB_MEMO, "filename": "語音備忘_發版檢查清單.m4a", "lines": MEMO, "model": "breeze-asr-25",
        "num_speakers": 1, "dictionary_ids": [1], "age_hours": 75.0, "process_ratio": 0.06,
        "speakers": {0: (None, None, None)},
    },
]

# 處理中與排隊中的任務（只有資料列，沒有逐字稿；demo 伺服器不啟動 worker，狀態不會變）
RUNNING_JOB = {"id": JOB_RUNNING, "filename": "使用者研究_訪談紀錄.m4a", "duration": 2412.0, "model": "breeze-asr-25",
               "progress": 0.43, "stage": "語音轉文字中"}
QUEUED_JOBS = [
    {"id": JOB_QUEUED_1, "filename": "季度回顧會議.m4a", "duration": 1836.0, "model": "breeze-asr-25"},
    {"id": JOB_QUEUED_2, "filename": "新人教育訓練.m4a", "duration": 927.0, "model": "large-v3-turbo"},
]

DICTIONARY_NAME = "產品用語"
DICTIONARY_CONTENT = """# 產品週會常用詞（示範字典）
onboarding = on boarding, 昂波丁
sprint = 斯普林特
staging = 史丹京
feature flag
API
dashboard
WebSocket
backlog
release note
"""


# ---- 音檔 ----


def audio_name(job_id: str) -> str:
    return f"{job_id}.m4a"


def make_audio(dest: Path, seconds: float) -> None:
    """用 ffmpeg 產生指定長度、極低音量的 220 Hz 正弦波（AAC／m4a）。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        sys.exit("找不到 ffmpeg：請先在本機產生音檔（demo_seed.py --audio-only），或安裝 ffmpeg")
    tmp = dest.with_suffix(".tmp.m4a")
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
         "-af", "volume=0.02", "-c:a", "aac", "-b:a", "32k", "-ac", "1", "-ar", "24000", str(tmp)],
        check=True,
    )
    tmp.replace(dest)


def ensure_audio_cache(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for spec in DONE_JOBS:
        dest = cache / audio_name(spec["id"])
        if not dest.exists():
            _, total = layout(spec["lines"])
            make_audio(dest, total)


# ---- 組逐字稿 ----


def build_transcript(spec: dict, embedding_model: str) -> tuple[dict, float]:
    segments, total = layout(spec["lines"])
    speakers: dict[str, dict] = {}
    embeddings: dict[str, list[float]] = {}
    for idx, (name, vp_seed, sim) in spec["speakers"].items():
        label = f"SPEAKER_{idx:02d}"
        speakers[label] = {"name": name, "voiceprint_id": None, "similarity": sim}
        embeddings[label] = make_embedding(vp_seed if vp_seed is not None else HOST_SEED + idx)
    transcript = {
        "version": 1,
        "language": "zh",
        "duration": float(total),
        "model": spec["model"],
        "text_source": {"type": "model", "model": spec["model"],
                        "label": "精準（Breeze-ASR-25）" if spec["model"] == "breeze-asr-25"
                        else "快速（Whisper large-v3-turbo）"},
        "diarization": {"enabled": True, "ok": True, "error": None, "num_speakers": spec["num_speakers"],
                        "detected_speakers": len(speakers)},
        "speakers": speakers,
        "speaker_embeddings": embeddings,
        "embedding_model": embedding_model,
        "segments": segments,
        "warnings": [],
    }
    return transcript, float(total)


# ---- 寫入 ----


def seed(data_dir: Path, audio_cache: Path) -> None:
    os.environ["DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(ROOT / "backend"))
    from app import db, dictionary_store, storage, voiceprint  # noqa: E402
    from app.config import settings  # noqa: E402
    from app.jobs import queue  # noqa: E402

    db.init_db()
    if queue.list_all():
        sys.exit(f"{data_dir} 已經有任務資料，不覆寫（請換一個空的資料目錄）")

    now = time.time()
    audio_cache.mkdir(parents=True, exist_ok=True)

    # 字典與聲紋（聲紋 id 由資料庫配發，逐字稿裡的 voiceprint_id 要對應）
    dictionary_store.create(DICTIONARY_NAME, DICTIONARY_CONTENT)
    vp_ids: dict[int, int] = {}
    for name, seed_no in VP_SEED.items():
        vp = voiceprint.create(name, make_embedding(seed_no), settings.embedding_model, "upload", 38.5)
        vp_ids[seed_no] = vp["id"]

    for spec in DONE_JOBS:
        transcript, total = build_transcript(spec, settings.embedding_model)
        transcript["embedding_model"] = settings.embedding_model
        for idx, (name, vp_seed, sim) in spec["speakers"].items():
            if vp_seed is not None:
                transcript["speakers"][f"SPEAKER_{idx:02d}"]["voiceprint_id"] = vp_ids[vp_seed]

        src = audio_cache / audio_name(spec["id"])
        if not src.exists():
            make_audio(src, total)
        job_dir = storage.job_dir(spec["id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, job_dir / "original.m4a")
        shutil.copyfile(src, storage.playback_path(spec["id"]))

        queue.create(filename=spec["filename"], model=spec["model"], diarize=True,
                     num_speakers=spec["num_speakers"], dictionary_ids=spec["dictionary_ids"], audio_ext=".m4a",
                     size_bytes=src.stat().st_size, job_id=spec["id"], duration=total)
        storage.save_original(spec["id"], transcript)
        created = now - spec["age_hours"] * 3600
        process = round(total * spec["process_ratio"] + 3.0, 3)
        queue.update(spec["id"], status="done", stage="完成", progress=1.0, created_at=created, queued_at=created,
                     started_at=created + 4, finished_at=created + 4 + process + 12, has_transcript=True,
                     has_playback=True, warnings=[], process_seconds=process, load_seconds=12.0,
                     position=0)

    # 處理中：只是一列資料，沒有 worker 去動它
    r = RUNNING_JOB
    queue.create(filename=r["filename"], model=r["model"], diarize=True, num_speakers=None, dictionary_ids=[1],
                 audio_ext=".m4a", size_bytes=int(r["duration"] * 4000), job_id=r["id"], duration=r["duration"])
    started = now - 25 * 60
    queue.update(r["id"], status="running", stage=r["stage"], progress=r["progress"], created_at=started - 60,
                 queued_at=started - 60, started_at=started, position=0)

    # 排隊中：建立順序＝排隊順序
    for i, q in enumerate(QUEUED_JOBS):
        queue.create(filename=q["filename"], model=q["model"], diarize=True, num_speakers=None,
                     dictionary_ids=[1] if i == 0 else [], audio_ext=".m4a",
                     size_bytes=int(q["duration"] * 4000), job_id=q["id"], duration=q["duration"])
        t = now - (20 - 3 * i) * 60
        queue.update(q["id"], created_at=t, queued_at=t)

    print(f"示範資料已建立：{data_dir}")
    for spec in DONE_JOBS:
        print(f"  已完成  /jobs/{spec['id']}  {spec['filename']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", help="資料目錄（SQLite 與音檔；預設讀 DATA_DIR 環境變數）")
    ap.add_argument("--audio-cache", required=True, help="示範音檔的存放目錄（已存在的檔案直接沿用）")
    ap.add_argument("--audio-only", action="store_true", help="只產生音檔到 --audio-cache，不寫資料庫")
    args = ap.parse_args()

    cache = Path(args.audio_cache)
    if args.audio_only:
        ensure_audio_cache(cache)
        print(f"音檔已備妥：{cache}")
        return
    data_dir = args.data_dir or os.environ.get("DATA_DIR")
    if not data_dir:
        sys.exit("請用 --data-dir 指定資料目錄")
    seed(Path(data_dir), cache)


if __name__ == "__main__":
    main()
