# Changelog

格式依 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版號依 [SemVer](https://semver.org/lang/zh-TW/)。

## [1.0.0] - 2026-11-22

### 新增
- 上傳音檔或貼上 YouTube 網址（含播放清單），背景佇列轉成逐字稿；關掉頁面也會繼續處理。
- 三種辨識模型：Breeze-ASR-25（預設，國語＋英文夾雜）、Whisper large-v3、large-v3-turbo；WhisperX 逐字對齊。
- 多人講者辨識（pyannote community-1）、講者平滑與邊界對齊、聲紋庫自動命名。
- 字典（專有名詞與替換規則，可複選）、OpenCC 繁體轉換。
- 結果頁：播放器同步捲動、逐字稿編輯、講者改名與合併、匯出 SRT／VTT／TXT／Markdown／JSON。
- 任務頁：處理中與排隊中的任務獨立一頁，排隊中的任務可調整順序與修改設定。
- Docker 部署腳本：部署前檢查進行中的任務，有任務時中止。
