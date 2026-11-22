"""佇列的預估等待時間。

估算方式（都是「約」，不保證精準）：
- 每個任務的處理時間 ≈ 固定開銷 + 速率 × 音檔秒數；換模型（或模型尚未載入）時再加上載入時間。
- 速率＝每秒音檔需要幾秒處理，依（模型, 是否講者辨識）分開算：
  用最近完成的任務實際耗時（`process_seconds`，已扣掉載入模型的時間）修正預設值：
      速率 = (Σ(實際處理秒數 − 固定開銷) + 預設速率 × PRIOR_AUDIO_SECONDS) / (Σ音檔秒數 + PRIOR_AUDIO_SECONDS)
  也就是把預設值當成「已經看過 PRIOR_AUDIO_SECONDS 秒音檔」的經驗，實際資料越多，預設值的影響越小；
  依音檔長度加權，長錄音的實測比短片段更有份量。沒有歷史資料時就是預設值。
- 預設速率來自這台機器（RTX 4070 Ti）的 benchmark：第二個測試檔（3016 秒）轉錄
  Breeze 約 172 秒、large-v3 約 180 秒、turbo 約 68 秒；講者辨識約 45 秒處理 2887 秒（第一個測試檔）。
- 處理中的任務：剩餘時間 ≈ 預估處理時間 × (1 − 目前進度)；它的辨識模型還沒載入時再加上載入時間。
- 排隊中的任務：等待時間 = 處理中任務的剩餘時間 + 排在前面每個任務的預估時間；完成時間再加上自己的預估時間。
- 音檔長度未知（ffprobe 讀不到）的任務無法估算，它和排在後面的任務都回傳 None。
- YouTube 來源：
  - 還要下載音訊的任務另外加上下載時間 ≈ 固定開銷（取得影片資訊、解 JS challenge）+ 下載速率 × 影片秒數，
    預設值是實測（2 分鐘影片約 3 秒取得資訊＋6 秒下載）抓的寬鬆值，同樣用最近的實際下載秒數（`download_seconds`）修正；
  - 有人工字幕而且要用字幕的任務不轉錄，速率用「字幕」這一組（只有講者辨識＋其他步驟），也不用載入 Whisper。
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from .. import db
from ..youtube import choose_caption

# benchmark 實測（秒／每秒音檔）
DEFAULT_ASR_RATE: dict[str, float] = {
    "breeze-asr-25": 172 / 3016,
    "large-v3": 180 / 3016,
    "large-v3-turbo": 68 / 3016,
}
FALLBACK_ASR_RATE = 180 / 3016  # 沒列在上面的模型
DEFAULT_DIARIZE_RATE = 45 / 2887  # 講者辨識＋聲紋
DEFAULT_OTHER_RATE = 0.01  # 轉檔、產生播放檔、逐字對齊、後處理
BASE_OVERHEAD = 2.0  # 每個任務固定開銷（ffprobe、寫檔等）；實測 55 秒片段在模型已載入時約 4 秒做完
DEFAULT_LOAD_SECONDS = 15.0  # 載入辨識＋對齊＋講者辨識模型（冷啟動實測約 14 秒）

# YouTube 下載（秒）：固定開銷＋每秒影片要幾秒下載
DEFAULT_DOWNLOAD_OVERHEAD = 8.0
DEFAULT_DOWNLOAD_RATE = 0.02
CAPTIONS_KEY = "captions"  # 用 YouTube 人工字幕（不轉錄）的任務在速率表裡的 key

PRIOR_AUDIO_SECONDS = 600.0  # 預設速率的權重：相當於看過 10 分鐘音檔
HISTORY_PER_KEY = 20  # 每種（模型, 講者辨識）只看最近幾筆
MIN_HISTORY_DURATION = 20.0  # 太短的片段固定開銷占比高，不拿來修正速率
ROUND_TO = 5  # 回傳的秒數取到 5 秒，避免 SSE 每秒都推送


def uses_captions(job: dict[str, Any]) -> bool:
    """預計走字幕路線：YouTube 來源、要用字幕、而且有符合優先順序的人工字幕。"""
    if job.get("source") != "youtube" or not job.get("use_captions"):
        return False
    return choose_caption(list(job.get("yt_manual_captions") or []), job.get("yt_language")) is not None


def rate_key(job: dict[str, Any]) -> str:
    """速率表的第一個 key：辨識模型，或走字幕路線時的 CAPTIONS_KEY。"""
    return CAPTIONS_KEY if uses_captions(job) else job["model"]


def asr_model(job: dict[str, Any]) -> str | None:
    """這個任務要載入的辨識模型；走字幕路線時不載入 Whisper。"""
    return None if uses_captions(job) else job["model"]


def default_rate(model: str, diarize: bool) -> float:
    asr = 0.0 if model == CAPTIONS_KEY else DEFAULT_ASR_RATE.get(model, FALLBACK_ASR_RATE)
    rate = asr + DEFAULT_OTHER_RATE
    if diarize:
        rate += DEFAULT_DIARIZE_RATE
    return rate


@dataclass
class Rates:
    """從歷史紀錄算出的速率與載入時間。"""

    # (model, diarize) → (Σ(處理秒數 − 固定開銷), Σ音檔秒數, 筆數)
    samples: dict[tuple[str, bool], tuple[float, float, int]] = field(default_factory=dict)
    load_seconds: float = DEFAULT_LOAD_SECONDS
    # YouTube 下載：(Σ(下載秒數 − 固定開銷), Σ影片秒數, 筆數)
    download: tuple[float, float, int] = (0.0, 0.0, 0)

    def download_rate(self) -> float:
        work, audio, _ = self.download
        return (work + DEFAULT_DOWNLOAD_RATE * PRIOR_AUDIO_SECONDS) / (audio + PRIOR_AUDIO_SECONDS)

    def download_seconds(self, job: dict[str, Any]) -> float:
        """還要下載音訊的 YouTube 任務的預估下載秒數；不用下載為 0（長度未知時只算固定開銷）。"""
        if job.get("source") != "youtube" or not job.get("download_pending"):
            return 0.0
        duration = job.get("duration") or 0.0
        return DEFAULT_DOWNLOAD_OVERHEAD + self.download_rate() * float(duration)

    def rate(self, model: str, diarize: bool) -> float:
        prior = default_rate(model, diarize)
        work, audio, _ = self.samples.get((model, diarize), (0.0, 0.0, 0))
        return (work + prior * PRIOR_AUDIO_SECONDS) / (audio + PRIOR_AUDIO_SECONDS)

    def has_history(self, model: str, diarize: bool) -> bool:
        return self.samples.get((model, diarize), (0, 0, 0))[2] > 0

    def process_seconds(self, job: dict[str, Any]) -> float | None:
        """不含載入模型的預估處理秒數（YouTube 還要下載時含下載時間）；音檔長度未知時回傳 None。"""
        duration = job.get("duration")
        if not duration or duration <= 0:
            return None
        return (BASE_OVERHEAD + self.rate(rate_key(job), bool(job["diarize"])) * float(duration)
                + self.download_seconds(job))


def download_share(job: dict[str, Any], rates: Rates | None = None) -> float:
    """下載占整個任務預估時間的比例：worker 把進度條的前面這一段給下載用。"""
    rates = rates or load_rates()
    total = rates.process_seconds({**job, "download_pending": True, "source": "youtube"})
    dl = rates.download_seconds({**job, "download_pending": True, "source": "youtube"})
    if not total:
        return 0.1
    return max(0.02, min(0.8, dl / total))


def load_rates() -> Rates:
    """讀最近完成任務的實際耗時。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT model, diarize, duration, process_seconds, load_seconds, caption_source, download_seconds"
            " FROM jobs WHERE status = 'done' AND process_seconds IS NOT NULL AND duration >= ?"
            " ORDER BY finished_at DESC LIMIT 500",
            (MIN_HISTORY_DURATION,),
        ).fetchall()
    return rates_from_history([dict(r) for r in rows])


def rates_from_history(rows: list[dict[str, Any]]) -> Rates:
    """rows 由新到舊排列。"""
    samples: dict[tuple[str, bool], tuple[float, float, int]] = {}
    loads: list[float] = []
    dl_work, dl_audio, dl_n = 0.0, 0.0, 0
    for r in rows:
        if r.get("download_seconds") is not None and dl_n < HISTORY_PER_KEY:
            dl_work += max(0.0, float(r["download_seconds"]) - DEFAULT_DOWNLOAD_OVERHEAD)
            dl_audio += float(r["duration"])
            dl_n += 1
        captions = str(r.get("caption_source") or "").startswith("manual")
        key = (CAPTIONS_KEY if captions else r["model"], bool(r["diarize"]))
        work, audio, n = samples.get(key, (0.0, 0.0, 0))
        if n < HISTORY_PER_KEY:
            samples[key] = (work + max(0.0, float(r["process_seconds"]) - BASE_OVERHEAD),
                            audio + float(r["duration"]), n + 1)
        if r.get("load_seconds") and r["load_seconds"] > 1.0 and len(loads) < 10:
            loads.append(float(r["load_seconds"]))
    load = sum(loads) / len(loads) if loads else DEFAULT_LOAD_SECONDS
    return Rates(samples=samples, load_seconds=load, download=(dl_work, dl_audio, dl_n))


def _round(x: float | None) -> int | None:
    if x is None:
        return None
    return int(max(0, round(x / ROUND_TO) * ROUND_TO))


def queue_info(active: list[dict[str, Any]], *, rates: Rates | None = None,
               loaded: Collection[str] | None = None) -> dict[str, dict[str, Any]]:
    """依領取順序（處理中在前，接著排隊中）計算每個任務的順位與預估時間。

    active 要包含所有排隊中與處理中的任務、照 queue.list_active() 的順序。
    loaded 是目前已載入的模型 key（ModelManager.loaded()），用來判斷第一個任務要不要載入模型；None 表示不知道。
    回傳 {job_id: {...}}：
      queue_position   前面還有幾個任務（含處理中）
      queue_rank       排隊順位，1＝下一個處理（處理中為 None）
      estimate_seconds 這個任務本身的預估處理秒數
      eta_start_seconds  預估幾秒後開始處理（處理中為 0）
      eta_finish_seconds 預估幾秒後完成
      estimate_basis   history（有用過去的實際耗時修正）／default（只有預設值）
    """
    rates = rates or load_rates()
    out: dict[str, dict[str, Any]] = {}
    running = [j for j in active if j["status"] == "running"]
    queued = [j for j in active if j["status"] == "queued"]

    clock: float | None = 0.0  # 從現在起算，前面的任務做完要幾秒；None＝無法估算
    prev_model: str | None = None
    for j in running:
        est = rates.process_seconds(j)
        remaining = None if est is None else est * (1.0 - max(0.0, min(1.0, float(j["progress"] or 0))))
        # 剛開始處理、辨識模型還在載入（ModelManager 裡還沒有）時，剩餘時間要算進載入時間
        model = asr_model(j)
        if remaining is not None and model and loaded is not None and f"whisper:{model}" not in loaded:
            remaining += rates.load_seconds
        clock = None if clock is None or remaining is None else clock + remaining
        out[j["id"]] = {
            "queue_position": None, "queue_rank": None,
            "estimate_seconds": _round(est), "eta_start_seconds": 0,
            "eta_finish_seconds": _round(clock),
            "estimate_basis": "history" if rates.has_history(rate_key(j), bool(j["diarize"])) else "default",
        }
        prev_model = model or prev_model

    for rank, j in enumerate(queued, start=1):
        est = rates.process_seconds(j)
        model = asr_model(j)
        if est is not None and model:
            if prev_model is None:
                needs_load = loaded is None or f"whisper:{model}" not in loaded
            else:
                needs_load = prev_model != model
            if needs_load:
                est += rates.load_seconds
        start = clock
        clock = None if clock is None or est is None else clock + est
        out[j["id"]] = {
            "queue_position": len(running) + rank - 1, "queue_rank": rank,
            "estimate_seconds": _round(est), "eta_start_seconds": _round(start),
            "eta_finish_seconds": _round(clock),
            "estimate_basis": "history" if rates.has_history(rate_key(j), bool(j["diarize"])) else "default",
        }
        prev_model = model or prev_model
    return out


EMPTY_INFO: dict[str, Any] = {
    "queue_position": None, "queue_rank": None, "estimate_seconds": None,
    "eta_start_seconds": None, "eta_finish_seconds": None, "estimate_basis": None,
}
