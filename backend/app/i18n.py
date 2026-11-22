"""介面語言（zh-TW／en）：訊息目錄、依請求決定語言、可延後翻譯的訊息。

- 給使用者看的文字一律用訊息代碼（code）＋參數（params）表示，例如
  `{"code": "job.not_found", "params": {}}`；存進資料庫的也是這個結構（JSON），讀出時依請求的語言 render。
- 參數可以是另一則訊息（巢狀，用同一個語言 render）或訊息／字串的清單（用該語言的頓號或逗號串起來）。
- 舊資料庫裡已經存好的中文字串照原樣顯示（`localize` 遇到一般字串直接回傳），不需要改寫舊資料。
- HTTP 錯誤用 `ApiError(status_code, code, **params)`；exception handler 依語言回傳
  `{"detail": 文字, "code": 代碼, "params": 參數}`。`detail` 仍是字串，前端讀 detail 的邏輯不用改。
"""
from __future__ import annotations

import json
import string
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

DEFAULT_LANG = "zh-TW"
LANGS = ("zh-TW", "en")

# code → {語言: 模板}；模板用 {name} 參數。英文用 sentence case、像一般產品的錯誤訊息，不逐字直譯。
MESSAGES: dict[str, dict[str, str]] = {
    # ---- 通用 ----
    "list.separator": {"zh-TW": "、", "en": ", "},
    "app.frontend_not_built": {"zh-TW": "前端尚未建置", "en": "The frontend hasn't been built yet."},
    "gpu.busy": {"zh-TW": "GPU 忙碌，請稍後重試", "en": "The GPU is busy. Try again later."},

    # ---- 上傳 ----
    "upload.too_large": {"zh-TW": "檔案超過 {limit_mb} MB", "en": "The file is larger than {limit_mb} MB."},
    "upload.empty": {"zh-TW": "檔案是空的", "en": "The file is empty."},

    # ---- 任務 ----
    "job.not_found": {"zh-TW": "找不到這筆紀錄", "en": "Job not found."},
    "job.batch_id.invalid": {"zh-TW": "批次代號格式錯誤", "en": "Invalid batch ID."},
    "job.dictionary_ids.invalid": {"zh-TW": "字典參數格式錯誤", "en": "Invalid dictionary list."},
    "job.model.unsupported": {"zh-TW": "不支援的模型：{model}", "en": "Unsupported model: {model}"},
    "job.model.not_installed": {"zh-TW": "模型 {label} 尚未安裝", "en": "The {label} model isn't installed."},
    "job.num_speakers.range": {
        "zh-TW": "講者人數要介於 {min} 到 {max}",
        "en": "The number of speakers must be between {min} and {max}.",
    },
    "job.move.target_required": {
        "zh-TW": "請指定 to、before、after 其中一個",
        "en": "Specify exactly one of to, before, or after.",
    },
    "job.options.already_started": {
        "zh-TW": "任務已經開始處理，無法修改設定",
        "en": "This job has already started, so its settings can't be changed.",
    },
    "job.options.already_finished": {
        "zh-TW": "任務已經結束，無法修改設定",
        "en": "This job has already finished, so its settings can't be changed.",
    },
    "job.options.dictionary_missing": {
        "zh-TW": "選擇的字典不存在（可能已被刪除），請重新選擇",
        "en": "A selected dictionary no longer exists. It may have been deleted, so choose again.",
    },
    "job.captions.unavailable": {
        "zh-TW": "這部影片沒有可用的人工字幕，只能用模型轉錄",
        "en": "This video has no usable manual captions, so it can only be transcribed with a model.",
    },
    "job.rerun.active": {"zh-TW": "任務還在排隊或執行中", "en": "This job is still queued or running."},
    "job.rerun.audio_deleted": {
        "zh-TW": "音檔已刪除，無法重新轉錄",
        "en": "The audio has been deleted, so this job can't be transcribed again.",
    },
    "job.audio.unavailable": {
        "zh-TW": "音檔已刪除或尚未產生",
        "en": "The audio has been deleted or isn't ready yet.",
    },
    "job.delete.busy": {
        "zh-TW": "任務排隊或執行中，請先取消再刪除",
        "en": "This job is queued or running. Cancel it before deleting.",
    },
    "batch.not_found": {"zh-TW": "找不到這個批次", "en": "Batch not found."},

    # ---- 排隊順序 ----
    "queue.move.running": {"zh-TW": "處理中的任務不能移動", "en": "A job that's being processed can't be moved."},
    "queue.move.not_queued": {"zh-TW": "只有排隊中的任務可以調整順序", "en": "Only queued jobs can be reordered."},
    "queue.move.target_gone": {
        "zh-TW": "目標任務已經開始處理或不在佇列中，請重新整理後再試",
        "en": "The target job has started or is no longer in the queue. Refresh and try again.",
    },

    # ---- 任務失敗原因（存在 jobs.error） ----
    "job.error.unexpected": {
        "zh-TW": "處理時發生未預期的錯誤，請重新轉錄",
        "en": "Something went wrong while processing. Try transcribing again.",
    },
    "job.error.restart_failed": {
        "zh-TW": "處理時程式中斷多次，可能是檔案過長或損壞",
        "en": "Processing was interrupted several times. The file may be too long or damaged.",
    },
    "job.error.audio_deleted": {
        "zh-TW": "音檔已刪除，無法轉錄",
        "en": "The audio has been deleted, so it can't be transcribed.",
    },
    "job.error.model_unavailable": {"zh-TW": "模型 {model} 無法使用", "en": "The {model} model isn't available."},
    "job.error.model_needed": {
        "zh-TW": "模型 {model} 無法使用（需要改用模型轉錄）",
        "en": "The {model} model isn't available, but this job needs it.",
    },
    "job.error.model_needed_reason": {
        "zh-TW": "模型 {model} 無法使用（{reason}，需要改用模型轉錄）",
        "en": "The {model} model isn't available, but this job needs it: {reason}.",
    },

    # ---- 處理步驟（jobs.stage） ----
    "stage.queued": {"zh-TW": "排隊中", "en": "Queued"},
    "stage.requeued": {"zh-TW": "排隊中（重新啟動後重新排隊）", "en": "Queued (requeued after a restart)"},
    "stage.preparing": {"zh-TW": "準備中", "en": "Preparing"},
    "stage.fetching_info": {"zh-TW": "取得影片資訊中", "en": "Fetching video info"},
    "stage.downloading_captions": {"zh-TW": "下載字幕中", "en": "Downloading captions"},
    "stage.downloading_audio": {"zh-TW": "下載音訊中", "en": "Downloading audio"},
    "stage.convert": {"zh-TW": "轉檔中", "en": "Converting"},
    "stage.transcribe": {"zh-TW": "轉錄中", "en": "Transcribing"},
    "stage.align": {"zh-TW": "逐字對齊中", "en": "Aligning words"},
    "stage.captions": {"zh-TW": "讀取 YouTube 字幕中", "en": "Reading YouTube captions"},
    "stage.diarize": {"zh-TW": "講者辨識中", "en": "Identifying speakers"},
    "stage.voiceprint": {"zh-TW": "聲紋比對中", "en": "Matching voiceprints"},
    "stage.postprocess": {"zh-TW": "整理結果中", "en": "Finalizing"},
    "stage.done": {"zh-TW": "完成", "en": "Done"},
    "stage.failed": {"zh-TW": "失敗", "en": "Failed"},
    "stage.canceled": {"zh-TW": "已取消", "en": "Canceled"},

    # ---- 處理過程的警告（jobs.warnings 與逐字稿的 warnings） ----
    "warning.playback_failed": {
        "zh-TW": "無法產生播放用音檔：{error}",
        "en": "Couldn't create the playback audio: {error}",
    },
    "warning.align_failed": {
        "zh-TW": "逐字對齊失敗（{lang}），改用段落時間：{error}",
        "en": "Word alignment failed ({lang}), so segment timings were used instead: {error}",
    },
    "warning.voiceprint_failed": {
        "zh-TW": "計算講者聲紋失敗，無法比對聲紋庫：{error}",
        "en": "Couldn't compute speaker voiceprints, so they weren't matched against the library: {error}",
    },
    "warning.dropped": {
        "zh-TW": "略過 {count} 段疑似提示詞幻覺（Whisper 把字典詞或提示句原樣吐出）：{items}{more}",
        "en": "Skipped {count} segment(s) that looked like hallucinated prompt text "
              "(Whisper repeated dictionary terms or the prompt verbatim): {items}{more}",
    },
    "warning.dropped_item": {"zh-TW": "[{time}]「{text}」", "en": "[{time}] \"{text}\""},
    "warning.dropped_more": {"zh-TW": "，另有 {count} 段", "en": ", plus {count} more"},

    # ---- 講者辨識 ----
    "diarize.failed": {"zh-TW": "講者辨識失敗：{error}", "en": "Speaker identification failed: {error}"},
    "diarize.no_token": {
        "zh-TW": "沒有設定 HF_TOKEN，無法下載 pyannote 講者辨識模型",
        "en": "HF_TOKEN isn't set, so the pyannote speaker identification model can't be downloaded.",
    },
    "diarize.gated": {
        "zh-TW": "pyannote 模型無法下載：請確認 HF_TOKEN 已設定，且已在 HuggingFace 同意 "
                 "pyannote/speaker-diarization-community-1 的使用條款",
        "en": "Couldn't download the pyannote model. Make sure HF_TOKEN is set and that you've accepted "
              "the terms for pyannote/speaker-diarization-community-1 on Hugging Face.",
    },
    "diarize.embedding_unavailable": {
        "zh-TW": "無法下載聲紋模型 {model}",
        "en": "Couldn't download the voiceprint model {model}.",
    },

    # ---- 音檔（ffmpeg） ----
    "audio.ffprobe_missing": {"zh-TW": "找不到 ffprobe", "en": "ffprobe isn't installed on the server."},
    "audio.ffmpeg_missing": {"zh-TW": "找不到 ffmpeg", "en": "ffmpeg isn't installed on the server."},
    "audio.unreadable": {"zh-TW": "無法讀取音檔：{detail}", "en": "Couldn't read the audio file: {detail}"},
    "audio.format_unsupported": {
        "zh-TW": "不支援這種檔案格式（{format}），請上傳一般的音訊或影片檔",
        "en": "This file format ({format}) isn't supported. Upload a regular audio or video file.",
    },
    "audio.no_stream": {"zh-TW": "檔案裡沒有音軌", "en": "The file has no audio track."},
    "audio.decode_failed": {
        "zh-TW": "所有音軌都無法解碼：{detail}",
        "en": "None of the audio tracks could be decoded: {detail}",
    },
    "audio.playback_failed": {
        "zh-TW": "產生播放檔失敗：{detail}",
        "en": "Couldn't create the playback file: {detail}",
    },
    "audio.wav_16bit_only": {"zh-TW": "只支援 16-bit WAV", "en": "Only 16-bit WAV is supported."},

    # ---- 逐字稿 ----
    "transcript.not_found": {
        "zh-TW": "沒有逐字稿（可能尚未完成或已刪除）",
        "en": "There's no transcript. It may not be finished yet, or it was deleted.",
    },
    "transcript.edit.busy": {
        "zh-TW": "重新轉錄中，完成前不能編輯",
        "en": "This job is being transcribed again. You can edit it once it's done.",
    },
    "transcript.edit.invalid": {"zh-TW": "編輯參數錯誤", "en": "Invalid edit request."},
    "transcript.edit.unsupported": {
        "zh-TW": "不支援的編輯操作：{op}",
        "en": "Unsupported edit operation: {op}",
    },
    "transcript.segment.not_found": {"zh-TW": "找不到段落 {id}", "en": "Segment {id} not found."},
    "transcript.speaker.not_found": {"zh-TW": "找不到講者 {label}", "en": "Speaker {label} not found."},
    "transcript.speaker.required": {
        "zh-TW": "要指定講者或輸入新講者名稱",
        "en": "Choose a speaker or enter a name for a new one.",
    },
    "transcript.merge.same": {
        "zh-TW": "來源與目標講者相同",
        "en": "The source and target speakers are the same.",
    },
    "transcript.merge.not_found": {
        "zh-TW": "找不到要合併的講者",
        "en": "Couldn't find the speakers to merge.",
    },
    "speaker.default": {"zh-TW": "講者 {n}", "en": "Speaker {n}"},

    # ---- 匯出 ----
    "export.format.unsupported": {"zh-TW": "不支援的匯出格式", "en": "Unsupported export format."},
    "export.default_title": {"zh-TW": "逐字稿", "en": "Transcript"},
    # 有講者時每一行的格式（TXT、SRT）
    "export.speaker_line": {"zh-TW": "{name}：{text}", "en": "{name}: {text}"},

    # ---- 字典 ----
    "dictionary.not_found": {"zh-TW": "找不到字典", "en": "Dictionary not found."},
    "dictionary.name_required": {"zh-TW": "請輸入字典名稱", "en": "Enter a dictionary name."},
    "dictionary.content_too_long": {"zh-TW": "字典內容太長", "en": "The dictionary is too long."},
    "dictionary.rule.invalid": {
        "zh-TW": "錯字與正字都要填，且不能相同",
        "en": "Enter both the wrong and the correct text, and make sure they're different.",
    },

    # ---- 聲紋 ----
    "voiceprint.not_found": {"zh-TW": "找不到聲紋", "en": "Voiceprint not found."},
    "voiceprint.name_required": {"zh-TW": "請輸入名稱", "en": "Enter a name."},
    "voiceprint.sample_too_large": {
        "zh-TW": "樣本檔案太大（上限 {limit_mb} MB）",
        "en": "The sample file is too large (the limit is {limit_mb} MB).",
    },
    "voiceprint.sample_too_short": {
        "zh-TW": "樣本太短（{seconds} 秒），至少要 {min} 秒。"
                 "請上傳 {min}–{max} 秒、只有這個人說話的錄音（超過 {max} 秒只會取前 {max} 秒）。",
        "en": "The sample is too short ({seconds} s). It needs at least {min} seconds. "
              "Upload {min}–{max} seconds of only this person speaking (only the first {max} seconds are used).",
    },
    "voiceprint.embed_failed": {
        "zh-TW": "計算聲紋失敗：{error}",
        "en": "Couldn't compute the voiceprint: {error}",
    },
    "voiceprint.speaker.no_embedding": {
        "zh-TW": "這位講者沒有聲紋資料（講話時間太短或講者辨識未成功）",
        "en": "This speaker has no voiceprint data. They may have spoken too briefly, "
              "or speaker identification didn't succeed.",
    },
    "voiceprint.speaker.not_original": {
        "zh-TW": "這位講者是手動新增或已經合併，沒有對應的聲紋資料",
        "en": "This speaker was added manually or merged, so there's no matching voiceprint data.",
    },

    # ---- 辨識模型（/api/models 的名稱與說明） ----
    "model.breeze-asr-25.label": {"zh-TW": "精準（Breeze-ASR-25）", "en": "Accurate (Breeze-ASR-25)"},
    "model.breeze-asr-25.description": {
        "zh-TW": "最適合台灣國語與中英夾雜，預設使用",
        "en": "Best for Taiwanese Mandarin and mixed Chinese–English. Used by default.",
    },
    "model.large-v3-turbo.label": {"zh-TW": "快速（Whisper large-v3-turbo）", "en": "Fast (Whisper large-v3-turbo)"},
    "model.large-v3-turbo.description": {
        "zh-TW": "速度約快 2.5 倍，同音錯字較多",
        "en": "About 2.5× faster, with more homophone errors.",
    },
    "model.large-v3.label": {"zh-TW": "多語（Whisper large-v3）", "en": "Multilingual (Whisper large-v3)"},
    "model.large-v3.description": {
        "zh-TW": "適合英文或其他語言為主的錄音",
        "en": "Best for recordings mostly in English or other languages.",
    },

    # ---- 語言名稱（字幕語言） ----
    "language.zh-tw": {"zh-TW": "台灣繁體中文", "en": "Traditional Chinese (Taiwan)"},
    "language.zh-hant": {"zh-TW": "繁體中文", "en": "Traditional Chinese"},
    "language.zh-hk": {"zh-TW": "香港繁體中文", "en": "Traditional Chinese (Hong Kong)"},
    "language.zh-mo": {"zh-TW": "澳門繁體中文", "en": "Traditional Chinese (Macau)"},
    "language.zh": {"zh-TW": "中文", "en": "Chinese"},
    "language.zh-hans": {"zh-TW": "簡體中文", "en": "Simplified Chinese"},
    "language.zh-cn": {"zh-TW": "簡體中文", "en": "Simplified Chinese"},
    "language.zh-sg": {"zh-TW": "簡體中文（新加坡）", "en": "Simplified Chinese (Singapore)"},
    "language.en": {"zh-TW": "英文", "en": "English"},
    "language.ja": {"zh-TW": "日文", "en": "Japanese"},
    "language.ko": {"zh-TW": "韓文", "en": "Korean"},
    "language.fr": {"zh-TW": "法文", "en": "French"},
    "language.de": {"zh-TW": "德文", "en": "German"},
    "language.es": {"zh-TW": "西班牙文", "en": "Spanish"},
    "language.vi": {"zh-TW": "越南文", "en": "Vietnamese"},
    "language.th": {"zh-TW": "泰文", "en": "Thai"},
    "language.id": {"zh-TW": "印尼文", "en": "Indonesian"},
    "language.ms": {"zh-TW": "馬來文", "en": "Malay"},
    "language.nan": {"zh-TW": "台語", "en": "Taiwanese Hokkien"},
    "language.hak": {"zh-TW": "客語", "en": "Hakka"},

    # ---- YouTube：網址 ----
    "youtube.resolve.empty": {"zh-TW": "請貼上 YouTube 網址", "en": "Paste a YouTube URL."},
    "youtube.url.empty": {"zh-TW": "網址是空的", "en": "The URL is empty."},
    "youtube.url.too_long": {"zh-TW": "網址太長", "en": "The URL is too long."},
    "youtube.url.malformed": {"zh-TW": "網址格式錯誤", "en": "The URL isn't valid."},
    "youtube.url.scheme": {
        "zh-TW": "只接受 http／https 的 YouTube 網址",
        "en": "Only http or https YouTube URLs are supported.",
    },
    "youtube.url.not_youtube": {
        "zh-TW": "只接受 YouTube 網址（youtube.com、youtu.be）",
        "en": "Only YouTube URLs (youtube.com, youtu.be) are supported.",
    },
    "youtube.url.unrecognized": {
        "zh-TW": "看不出是哪一部影片或播放清單，請貼影片或播放清單的網址",
        "en": "Couldn't tell which video or playlist this is. Paste a video or playlist URL.",
    },
    "youtube.entry.unavailable": {"zh-TW": "私人或已刪除的影片", "en": "Private or deleted video"},
    "youtube.entry.live": {"zh-TW": "直播尚未結束", "en": "The livestream hasn't ended"},

    # ---- YouTube：無法處理的影片、下載失敗 ----
    "youtube.error.invalid_id": {"zh-TW": "影片 ID 格式錯誤", "en": "Invalid video ID."},
    "youtube.error.private": {
        "zh-TW": "這是私人影片，只有上傳者授權的帳號能看。請確認網址，或請上傳者改成公開或「不公開（知道連結的人可以看）」後再試。",
        "en": "This is a private video that only accounts approved by the uploader can watch. Check the URL, "
              "or ask the uploader to make it public or unlisted, then try again.",
    },
    "youtube.error.members_only": {
        "zh-TW": "這是頻道會員限定的影片，需要會員帳號才能觀看，目前不支援。可以自行取得音檔後用上傳檔案的方式轉錄。",
        "en": "This video is for channel members only, which isn't supported. "
              "You can get the audio yourself and upload it as a file instead.",
    },
    "youtube.error.age_restricted": {
        "zh-TW": "這部影片有年齡限制，需要登入 YouTube 才能觀看，目前不支援。可以自行取得音檔後用上傳檔案的方式轉錄。",
        "en": "This video is age-restricted and requires signing in to YouTube, which isn't supported. "
              "You can get the audio yourself and upload it as a file instead.",
    },
    "youtube.error.geo_restricted": {
        "zh-TW": "這部影片有地區限制，在伺服器所在的地區（台灣）無法觀看。",
        "en": "This video is region-locked and isn't available where the server is (Taiwan).",
    },
    "youtube.error.live_not_ended": {
        "zh-TW": "直播或首播還沒結束（或還沒開始），YouTube 還沒有完整的影片可以下載。請等直播結束、YouTube 處理完成後再試。",
        "en": "The livestream or premiere hasn't ended (or hasn't started), so there's no full video to download yet. "
              "Try again after it ends and YouTube finishes processing it.",
    },
    "youtube.error.live_in_progress": {
        "zh-TW": "直播還在進行中，YouTube 還沒有完整的影片。請等直播結束、YouTube 處理完成後再試。",
        "en": "This livestream is still in progress, so there's no full video yet. "
              "Try again after it ends and YouTube finishes processing it.",
    },
    "youtube.error.live_upcoming": {
        "zh-TW": "直播或首播還沒開始，沒有內容可以下載。請等播出結束後再試。",
        "en": "This livestream or premiere hasn't started yet, so there's nothing to download. "
              "Try again after it has aired.",
    },
    "youtube.error.post_live": {
        "zh-TW": "直播剛結束，YouTube 還在處理影片，暫時無法完整下載。請過幾個小時再試。",
        "en": "This livestream just ended and YouTube is still processing it, so it can't be fully downloaded yet. "
              "Try again in a few hours.",
    },
    "youtube.error.payment": {
        "zh-TW": "這是付費（購買或租借）內容，無法下載。",
        "en": "This is paid content (purchase or rental), so it can't be downloaded.",
    },
    "youtube.error.premium_only": {
        "zh-TW": "這是 YouTube Premium 限定內容，無法下載。",
        "en": "This content is for YouTube Premium only, so it can't be downloaded.",
    },
    "youtube.error.playlist_missing": {
        "zh-TW": "播放清單不存在或是私人清單。請確認網址，或把清單改成公開／不公開後再試。",
        "en": "The playlist doesn't exist or is private. Check the URL, or make the playlist public or unlisted, "
              "then try again.",
    },
    "youtube.error.removed": {
        "zh-TW": "影片已被下架、刪除或不存在。請確認網址是否正確。",
        "en": "This video was taken down, deleted, or doesn't exist. Check that the URL is correct.",
    },
    "youtube.error.bot_check": {
        "zh-TW": "YouTube 暫時要求驗證（懷疑是機器人），所以拒絕下載。請過一段時間再試；如果一直發生，可能需要更新 yt-dlp 或設定 PO Token（見 README）。",
        "en": "YouTube is temporarily asking for verification (it suspects a bot), so the download was refused. "
              "Try again later. If this keeps happening, yt-dlp may need an update or a PO Token "
              "(see the README).",
    },
    "youtube.error.rate_limited": {
        "zh-TW": "YouTube 暫時限制了這台伺服器的請求次數（最長約 1 小時），請稍後再試。",
        "en": "YouTube is temporarily rate-limiting this server (for up to about an hour). Try again later.",
    },
    "youtube.error.expired": {
        "zh-TW": "YouTube 回應找不到檔案（HTTP 404／410），通常是下載網址已過期。請重新轉錄再試一次。",
        "en": "YouTube couldn't find the file (HTTP 404/410), which usually means the download link expired. "
              "Transcribe it again to retry.",
    },
    "youtube.error.forbidden": {
        "zh-TW": "YouTube 拒絕下載（HTTP 403），通常是 YouTube 改版造成。請稍後再試；如果一直發生，需要更新 yt-dlp。",
        "en": "YouTube refused the download (HTTP 403), usually because of a change on YouTube's side. "
              "Try again later. If this keeps happening, yt-dlp needs an update.",
    },
    "youtube.error.network": {
        "zh-TW": "連不上 YouTube，請確認伺服器的網路後再試。",
        "en": "Couldn't reach YouTube. Check the server's network connection and try again.",
    },
    "youtube.error.too_large": {
        "zh-TW": "音訊檔超過大小上限，無法下載。",
        "en": "The audio file is over the size limit, so it can't be downloaded.",
    },
    "youtube.error.no_format": {
        "zh-TW": "找不到可以下載的音訊格式（可能是 YouTube 改版，需要更新 yt-dlp）。",
        "en": "No downloadable audio format was found. YouTube may have changed, and yt-dlp may need an update.",
    },
    "youtube.error.unknown": {"zh-TW": "YouTube 下載失敗：{detail}", "en": "YouTube download failed: {detail}"},
    "youtube.error.unknown_detail": {"zh-TW": "未知錯誤", "en": "unknown error"},
    "youtube.error.unparsable_output": {
        "zh-TW": "無法解析 yt-dlp 的輸出",
        "en": "couldn't parse the yt-dlp output",
    },
    "youtube.error.no_audio_file": {"zh-TW": "下載後找不到音訊檔", "en": "no audio file was found after downloading"},
    "youtube.error.bad_data": {
        "zh-TW": "YouTube 回傳的資料格式不對",
        "en": "YouTube returned data in an unexpected format.",
    },
    "youtube.error.ytdlp_missing": {
        "zh-TW": "伺服器沒有安裝 yt-dlp",
        "en": "yt-dlp isn't installed on the server.",
    },
    "youtube.error.timeout": {
        "zh-TW": "連線 YouTube 逾時，請稍後再試。",
        "en": "The connection to YouTube timed out. Try again later.",
    },

    # ---- YouTube：字幕 ----
    "youtube.caption_label": {"zh-TW": "YouTube 人工字幕（{language}）", "en": "YouTube manual captions ({language})"},
    "youtube.caption_label_plain": {"zh-TW": "YouTube 人工字幕", "en": "YouTube manual captions"},
    # 字幕不能用、改用模型轉錄時的警告；`.reason` 是同一件事不含「已改用模型轉錄」的說法（模型也不能用時組失敗原因）
    "youtube.captions.missing": {
        "zh-TW": "無法下載 YouTube 人工字幕（{language}），已改用模型轉錄。",
        "en": "Couldn't download the YouTube manual captions ({language}), so a model was used instead.",
    },
    "youtube.captions.missing.reason": {
        "zh-TW": "無法下載 YouTube 人工字幕（{language}）",
        "en": "couldn't download the YouTube manual captions ({language})",
    },
    "youtube.captions.failed": {
        "zh-TW": "YouTube 人工字幕（{language}）下載失敗（{reason}），已改用模型轉錄。",
        "en": "Downloading the YouTube manual captions ({language}) failed ({reason}), so a model was used instead.",
    },
    "youtube.captions.failed.reason": {
        "zh-TW": "YouTube 人工字幕（{language}）下載失敗（{reason}）",
        "en": "downloading the YouTube manual captions ({language}) failed ({reason})",
    },
    "youtube.captions.empty": {
        "zh-TW": "YouTube 人工字幕（{language}）沒有內容，改用模型轉錄。",
        "en": "The YouTube manual captions ({language}) are empty, so a model was used instead.",
    },
    "youtube.captions.empty.reason": {
        "zh-TW": "YouTube 人工字幕（{language}）沒有內容",
        "en": "the YouTube manual captions ({language}) are empty",
    },
    "youtube.captions.parse_failed": {
        "zh-TW": "無法解析 YouTube 字幕，改用模型轉錄：{error}",
        "en": "Couldn't parse the YouTube captions, so a model was used instead: {error}",
    },
    "youtube.captions.parse_failed.reason": {
        "zh-TW": "無法解析 YouTube 字幕：{error}",
        "en": "couldn't parse the YouTube captions: {error}",
    },
    # 警告裡的簡短原因
    "youtube.reason.rate_limited": {"zh-TW": "HTTP 429，請求次數過多", "en": "HTTP 429, too many requests"},
    "youtube.reason.timeout": {"zh-TW": "逾時", "en": "timed out"},
    "youtube.reason.network": {"zh-TW": "連線失敗", "en": "connection failed"},
    "youtube.reason.bot_check": {"zh-TW": "YouTube 要求驗證", "en": "YouTube asked for verification"},
    "youtube.reason.forbidden": {"zh-TW": "HTTP 403", "en": "HTTP 403"},
    "youtube.reason.expired": {"zh-TW": "下載網址失效", "en": "download link expired"},
}


Message = dict[str, Any]  # {"code": str, "params": dict}


def msg(code: str, **params: Any) -> Message:
    """可延後翻譯的訊息：存進資料庫或丟出例外時用，要顯示時再依語言 render。"""
    return {"code": code, "params": params}


def is_message(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("code"), str)


def _param(value: Any, lang: str) -> str:
    # 只有 dict 才當成巢狀訊息；字串一律原樣（逐字稿文字、講者名剛好是訊息 JSON 時不能被替換）。
    # 巢狀訊息存進資料庫時是整則訊息一起 dumps，讀回後參數仍是 dict。
    if is_message(value):
        return localize(value, lang)
    if isinstance(value, (list, tuple)):
        return render("list.separator", {}, lang).join(_param(v, lang) for v in value)
    return "" if value is None else str(value)


class _Params(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(code: str, params: dict[str, Any] | None, lang: str) -> str:
    """依語言 render；沒有這個語言時退回 zh-TW，沒有這個 code 時回傳 code 本身。"""
    templates = MESSAGES.get(code)
    if not templates:
        return code
    template = templates.get(lang) or templates.get(DEFAULT_LANG) or code
    values = _Params({k: _param(v, lang) for k, v in (params or {}).items()})
    try:
        return template.format_map(values)
    except (ValueError, IndexError):  # 模板寫錯（例如單獨的大括號）時不要讓 API 500
        return template


def decode(value: Any) -> Any:
    """資料庫讀出的欄位：訊息的 JSON 字串轉回 dict；其他（舊資料的中文字串、None）原樣回傳。"""
    if isinstance(value, str) and value.startswith('{"code"'):
        try:
            data = json.loads(value)
        except ValueError:
            return value
        if is_message(data):
            return data
    return value


def dumps(message: Message) -> str:
    """存進資料庫的格式（JSON 字串）。"""
    return json.dumps(message, ensure_ascii=False)


def localize(value: Any, lang: str) -> Any:
    """訊息（dict 或其 JSON 字串）依語言 render；一般字串（舊資料）與 None 原樣回傳。"""
    value = decode(value)
    if is_message(value):
        params = value.get("params")
        return render(value["code"], params if isinstance(params, dict) else {}, lang)
    return value


def localize_list(values: Any, lang: str) -> list[Any]:
    return [localize(v, lang) for v in values or []]


def has(code: str) -> bool:
    return code in MESSAGES


# ---- 依請求決定語言 ----


def normalize_lang(value: str | None) -> str | None:
    """zh 開頭 → zh-TW，其他 → en；空字串或萬用字元回傳 None（交給下一個來源判斷）。"""
    v = (value or "").strip().lower()
    if not v or v == "*":
        return None
    return "zh-TW" if v.startswith("zh") else "en"


def _from_accept_language(header: str) -> str | None:
    best: tuple[float, int, str] | None = None
    for i, part in enumerate(header.split(",")):
        tag, _, rest = part.strip().partition(";")
        q = 1.0
        for p in rest.split(";"):
            k, _, v = p.strip().partition("=")
            if k.strip().lower() == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        lang = normalize_lang(tag)
        if lang is None or q <= 0:
            continue
        # q 大的優先，相同時照出現順序
        if best is None or q > best[0]:
            best = (q, i, lang)
    return best[2] if best else None


def resolve_lang(request: Request | None) -> str:
    """請求要用的語言：query `lang` → `Accept-Language` → zh-TW（維持舊行為）。"""
    query = getattr(request, "query_params", None)
    lang = normalize_lang(query.get("lang")) if query is not None else None
    if lang is not None:
        return lang
    headers = getattr(request, "headers", None)  # 沒有 query／headers（直接呼叫端點、測試）時用預設
    return _from_accept_language((headers.get("accept-language") if headers is not None else None) or "") \
        or DEFAULT_LANG


# ---- 例外 ----


class LocalizedError(Exception):
    """訊息可以直接顯示給使用者的例外：`msg` 是可延後翻譯的訊息，`str(e)` 是 zh-TW 文字（log 用）。

    第一個參數不是已知的 code 時當成一般文字（相容直接給中文訊息的舊寫法）。
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.msg: Message | str = msg(code, **params) if code in MESSAGES else code
        super().__init__(localize(self.msg, DEFAULT_LANG))


def message_of(exc: BaseException) -> Message | str:
    """例外要顯示給使用者的訊息：有 msg 屬性（LocalizedError、YouTubeError）就用它，否則用 str(e)。"""
    m = getattr(exc, "msg", None)
    return m if m is not None else str(exc)


class ApiError(HTTPException):
    """要依語言回應的 HTTP 錯誤：`ApiError(404, "job.not_found")`、`ApiError(400, "upload.too_large", limit_mb=10)`。

    code 也可以直接給訊息 dict（例如例外的 msg）或一般文字（沒有翻譯，code 回 null）。
    `detail` 是 zh-TW 文字，沒有掛 exception handler 時行為跟原本的 HTTPException 一樣。
    """

    def __init__(self, status_code: int, code: Message | str, **params: Any) -> None:
        if is_message(code):
            self.message: Message | str = code  # type: ignore[assignment]
        elif isinstance(code, str) and code in MESSAGES:
            self.message = msg(code, **params)
        else:
            self.message = str(code)
        super().__init__(status_code, localize(self.message, DEFAULT_LANG))

    def payload(self, lang: str) -> dict[str, Any]:
        return error_payload(self.message, lang)


def error_payload(message: Message | str, lang: str) -> dict[str, Any]:
    if is_message(message):
        return {"detail": localize(message, lang), "code": message["code"],  # type: ignore[index]
                "params": message.get("params") or {}}  # type: ignore[union-attr]
    return {"detail": message, "code": None, "params": {}}


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.payload(resolve_lang(request)), status_code=exc.status_code, headers=exc.headers)


def template_fields(template: str) -> set[str]:
    """模板裡用到的參數名稱（測試檢查兩個語言的參數一致用）。"""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}
