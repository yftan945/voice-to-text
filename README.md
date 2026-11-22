# voice-to-text

[![Release](https://img.shields.io/github/v/release/yftan945/voice-to-text)](https://github.com/yftan945/voice-to-text/releases)
[![Last commit](https://img.shields.io/github/last-commit/yftan945/voice-to-text)](https://github.com/yftan945/voice-to-text/commits)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Docker](https://img.shields.io/badge/Docker-NVIDIA%20GPU-2496ED?logo=docker&logoColor=white)

區網用的語音轉文字網站：上傳音檔或貼上 YouTube 網址，背景轉成逐字稿，支援國語＋英文夾雜、多人講者辨識、聲紋庫、字典、時間軸字幕與匯出。部署在一台有 NVIDIA GPU 的主機（開發時用 RTX 4070 Ti 12GB）。


## 功能

- **上傳**：任何 ffmpeg 可解碼的格式（含 iPhone 語音備忘錄 `.qta`）；選辨識模型、多人辨識開關（預設開）、講者人數（自動／指定 N）、字典（可複選）。上傳後在背景處理，關掉頁面也會繼續跑。
- **批次上傳**：一次選多個檔案或拖進多個檔案；模型、多人辨識、字典整批共用，每個檔案可以個別指定講者人數（預設跟著整批）或從清單移除。檔案一次傳一個（避免手機頻寬被塞滿），每個檔案有自己的上傳進度（百分比、已傳／總大小），另有整批進度（第幾個／共幾個）。單一檔案失敗不影響其他檔案，失敗的那列顯示原因、可以重試（檔案太大、沒有音軌這類重試也沒用的錯誤不顯示重試）。上傳中離開頁面會先確認。全部完成後：只有一個檔案時到它的結果頁，多個檔案時留在首頁（下面的佇列就看得到）。同一批的任務共用 `batch_id`。
- **YouTube 網址**（首頁「貼上 YouTube 網址」；手機在底部的「貼上網址」）：一行一個，支援 `youtube.com/watch`、`youtu.be`、`/shorts/`、`/live/`、`/embed/`、播放清單（`list=`）。單一影片直接加進清單（顯示標題、頻道、長度、有沒有人工字幕）；播放清單先列出所有影片（逐支檢查人工字幕，私人／已刪除／直播中的影片不能勾），勾選後加入。影片和上傳的檔案共用整批設定、同一個 `batch_id`，每支也可以個別指定講者人數。
  - **下載**：伺服器用 yt-dlp 下載最佳音質的純音訊（之後照一般流程轉成 WAV 與播放用 m4a）。下載是任務的第一個步驟（「取得影片資訊中」「下載音訊中 N%」），可以取消；音訊跟上傳的檔案一樣永久保留。
  - **字幕**：有**人工字幕**時直接用字幕的文字與時間，不跑 Whisper（不載入辨識模型），語言優先順序 zh-TW → zh-Hant（含 zh-HK、zh-MO）→ zh → zh-Hans（含 zh-CN、zh-SG）→ en（含 en-US 等）→ 影片原始語言；**只有自動字幕或沒有字幕**時用選的模型轉錄（自動字幕一律不用）。字幕仍會跑講者辨識，依時間重疊把講者套到字幕段落（一段橫跨兩位講者時取重疊時間加總較多的；字幕沒有逐字時間就不切句），聲紋比對、OpenCC、全形標點、字典替換照常。
  - **結果頁**：標示「文字來源：YouTube 人工字幕（台灣繁體中文）」或模型名稱，以及頻道、「在 YouTube 開啟」。嵌入 YouTube 播放器（IFrame Player API、`youtube-nocookie.com`；桌機在右側講者側欄上方，手機是右下角可收合的小視窗），點段落跳到影片的時間點、播放中的段落跟著影片反白。影片無法嵌入（擁有者禁止嵌入、下架、私人）或播放器載入失敗時，自動改用保留的音訊並說明原因；也可以手動切換「影片／音訊」。
  - **重新轉錄**可以選「用 YouTube 人工字幕」或「用模型轉錄」（只有在有符合優先順序的人工字幕時才有前者）；音訊與字幕已經在伺服器上，不會再連 YouTube。上次下載失敗的任務重新轉錄會重新下載。
  - **錯誤**：私人影片、年齡限制、會員限定、下架／不存在、地區限制、直播或首播尚未結束、YouTube 要求驗證、請求次數限制、HTTP 403、連不上 YouTube 都有對應的說明與處理方式；下載失敗只會讓該任務失敗，worker 繼續處理下一個。
- **進度**：SSE 即時推送（排隊順位、目前步驟、百分比、預估時間），可取消。
- **佇列**（首頁「處理中與排隊中」）：每個任務的階段、進度條、排隊順位「第 N 位」、預估開始／完成時間；同一批的任務另外顯示「已完成幾個、共幾個」與整批預估完成時間。可以單筆取消（處理中的那筆會先確認）、整批取消（有確認對話框）；排隊中的任務可以拖曳調整順序（桌機）、「移到最前」、「上移／下移」（手機與鍵盤）。處理中的那一筆不能移動。結果頁的進度卡也顯示順位與預估時間。
- **結果頁**
  - 講者色帶（sticky，兼任進度列）：點擊或拖曳跳轉、←→ 前後 5 秒；逐字稿是講者頭像加泡泡，點泡泡從那裡播放、播放中的泡泡用鈷藍細線標示（可選「跟隨播放」自動捲動，暫停時跳轉也會捲過去）。
  - 「時間碼」開關：開啟時泡泡旁顯示時間碼；關閉時同一位講者的話接成段落（接法同 TXT 匯出）。兩種模式都能播放與編輯。
  - 編輯：講者改名、逐段改文字、改單段講者（可新增講者）、合併講者。原始結果保留，編輯另存，可一鍵還原。
  - 改字後會自動找出「錯字 → 正字」，可一鍵加入指定字典。
  - 講者「存成聲紋」到聲紋庫；已比對到聲紋的講者改成「更新聲紋」（更新那一筆，不新增重複的）。手動新增的講者代號是 `MANUAL_xx`，沒有聲紋可存。
  - 任務排隊或重新轉錄中時結果頁為唯讀。
  - 匯出 TXT／Markdown／SRT／VTT／JSON（用編輯後的版本；TXT 依「時間碼」開關決定有沒有時間戳）。純文字模式與 TXT：同一位講者的兩段中文之間沒有句末標點時補「，」，中文與英文或數字相接時補空格，停頓超過 2 秒換行。
  - 講者辨識失敗（例如 pyannote 模型無法下載）時仍輸出無講者的逐字稿，並在結果頁顯示原因。
- **已完成的錄音**（首頁「已完成」，舊網址 `/history` 轉到首頁）：每筆有迷你講者色帶與占比圖例；永久保留；刪除三選一（只刪逐字稿、只刪音檔、整筆刪除）；可用其他模型／字典重新轉錄（需要音檔還在）。
- **字典**：多份字典，語法同 Umless：每行一個詞；`正字 = 誤聽1, 誤聽2` 為替換規則；`#` 開頭為註解。辨識前當提示詞（hotwords）、辨識後自動替換。
- **聲紋庫**：上傳 10–30 秒單人樣本登記，或從結果頁講者存入；轉錄時自動比對，相似度超過門檻才標真名，否則維持代號 `SPEAKER_xx`（畫面與匯出檔顯示成「講者 N」，N＝代號數字＋1，和頭像上的數字一致；代號只在側欄的統計那一行出現）。

## 架構

```
瀏覽器（React + Vite + TS，RWD）── YouTube IFrame Player（youtube-nocookie.com）
   │  REST + SSE
   ▼
FastAPI（uvicorn，單一容器）─── 靜態檔（前端 dist）
   │
   ├─ SQLite（data/voice-to-text.sqlite3）：任務佇列、逐字稿（原始＋編輯）、字典、聲紋
   ├─ data/audio/<job>/original.<ext>、playback.m4a
   │
   └─ GPU worker 執行緒（一次一個任務）
        （YouTube）yt-dlp 子行程：取得影片資訊（2 小時內的快取直接沿用）→ 下載人工字幕 → 下載音訊
                   → data/audio/<job>/captions.<語言>.vtt、original.<ext>（字幕失敗改用模型轉錄）
        ffmpeg（-map 0:a:N，逐條嘗試第一條可解碼音軌）→ 16kHz WAV
        （有人工字幕時跳過下面兩步）
        → faster-whisper（自動偵測語言、字典詞彙放 hotwords）
        → WhisperX 逐字對齊（依段落語言分組；中文以字為單位）
        → pyannote.audio 4.x community-1 講者分段（exclusive diarization）
        → 逐字指派講者、換講者處切句、極短誤判平滑（字幕：依重疊時間整段指派）
        → 講者聲紋（wespeaker embedding）與聲紋庫 cosine 比對
        → 繁體轉換（逐段判斷 s2twp／t2tw＋台灣慣用字保護表；繁體字幕不轉）→ 中文旁半形標點轉全形 → 字典替換
```

後端模組（`backend/app/`）：

| 模組 | 內容 |
|---|---|
| `api/` | `jobs.py`（上傳、SSE、取消、重跑、刪除、音檔、逐字稿編輯、匯出、存聲紋）、`youtube.py`（解析網址、展開播放清單、影片資訊快取、建立 YouTube 任務）、`dictionaries.py`、`voiceprints.py`、`system.py` |
| `youtube.py` | YouTube 網址解析與驗證、字幕語言優先順序、yt-dlp 錯誤訊息對應（純 Python） |
| `youtube_cache.py` | 影片資訊的記憶體快取（API 與 worker 共用；成功 2 小時、失敗 5 分鐘） |
| `pipeline/ytdlp.py` | 以子行程執行 yt-dlp（進度、取消、逾時；只用 YouTube extractor） |
| `pipeline/youtube_source.py` | worker 的下載步驟：缺什麼才下載什麼（字幕、音訊分開下載），字幕失敗改用模型轉錄，重新轉錄不連 YouTube |
| `pipeline/captions.py` | VTT／SRT 解析（去重複行、拿掉標記）與講者套用到字幕段落（純 Python） |
| `jobs/queue.py` | SQLite 佇列：建立、依 `position` 領取（交易）、調整順序、重新排隊、取消、整批取消 |
| `jobs/estimate.py` | 佇列的預估等待時間（預設速度＋過去任務實際耗時修正） |
| `jobs/worker.py` | 單一 GPU worker 執行緒；啟動時把 `running` 任務放回佇列（同一任務最多 2 次，再中斷就標為失敗）；迴圈出錯不會讓執行緒死掉 |
| `pipeline/audio.py` | ffmpeg／ffprobe：轉 WAV、產生播放用 m4a |
| `pipeline/asr.py`、`align.py`、`diarize.py` | faster-whisper、WhisperX、pyannote（只在用到時 import） |
| `pipeline/assign.py` | 逐字指派講者與切句（純 Python） |
| `pipeline/postprocess.py` | 繁體轉換（OpenCC 規則、台灣慣用字保護表）、全形標點、字典 |
| `pipeline/models.py` | 模型快取：用到才載入、閒置 10 分鐘卸載並 `torch.cuda.empty_cache()` |
| `pipeline/backend.py` | `Backend` 介面與 `GpuBackend`；測試用假後端取代即可在無 GPU 環境跑完整流程 |
| `pipeline/runner.py` | 流程編排、進度、OOM → 「GPU 忙碌，請稍後重試」 |
| `dictionary.py`、`dictionary_store.py` | 字典解析／合併／替換（純 Python）與存取 |
| `voiceprint.py` | 聲紋比對（一對一貪婪配對）與存取 |
| `transcript.py` | 逐字稿編輯操作 |
| `export.py` | TXT／Markdown／SRT／VTT／JSON |
| `storage.py` | 檔案位置、逐字稿存取、三種刪除 |

### 一些設計決定

- **先 OpenCC 再字典替換**（spec 寫的是字典 → OpenCC）：避免 OpenCC 把使用者寫的正字改掉（例如「台」→「臺」）。誤聽會同時比對原寫法與各種轉換後的寫法（s2twp＋保護表、t2tw、只轉標點），所以字典寫簡體或繁體、段落有沒有跑 s2twp 都能命中。
- **繁體轉換規則**（`pipeline/postprocess.py`）：OpenCC `s2twp` 會把台灣常見的寫法改成教育部標準字（台灣→臺灣、電視台→電視臺、了解→瞭解），所以不是每段都跑：
  - **字幕路線**：字幕語言是 `zh-TW`、`zh-Hant`、`zh-HK`、`zh-MO`（含 `zh-Hant-TW` 這類變體）時完全不跑 OpenCC，只做全形標點與字典替換；`zh-Hans`、`zh-CN`、`zh-SG` 與不分區的 `zh` 照下面逐段判斷；其他語言（英文、日文…）不跑 OpenCC。
  - **模型路線與簡體字幕：逐段判斷**。段落裡已經有繁體專用字（`t2s(文字) != 文字`）就不跑 `s2twp`，只用 `t2tw` 統一異體字（爲→為、裏→裡）；沒有繁體專用字才跑 `s2twp`，再套台灣慣用字保護表。限制：同一段裡簡繁混雜（例如「這个」）時，因為有繁體字，簡體字不會被轉換。
  - **台灣慣用字保護表**（`TAIWAN_PREFERRED`，只用在 `s2twp` 轉過的段落）：臺灣→台灣、臺北→台北、臺中→台中、臺南→台南、臺東→台東、臺語→台語、臺幣→台幣、臺大→台大、臺詞→台詞、電視臺→電視台、平臺→平台、舞臺→舞台、後臺→後台、瞭解→了解。**不會**把「臺」全部換成「台」；正式名稱（`KEEP_OFFICIAL`）保留「臺」：國立臺…（國立臺灣大學、國立臺北科技大學等）、臺灣大學、臺灣師範大學、臺灣科技大學、臺灣銀行、臺灣土地銀行、臺灣證券交易所、臺灣高等法院、新臺幣、臺北市政府、臺中市政府、臺南市政府、臺東縣政府。要增減時改 `postprocess.py` 的兩份清單並同步這裡；個別用語也可以用字典處理（字典在 OpenCC 之後執行）。
- **提示詞放在 faster-whisper 的 `hotwords`**，並關閉 `condition_on_previous_text`：`initial_prompt` 在這個模式下只影響第一個 30 秒視窗，`hotwords` 每個視窗都會帶入；關閉前文條件可降低長錄音的重複幻覺。整段只是提示句或提示詞拼起來的段落會被當成幻覺略過。
- **語言自動偵測＋逐視窗偵測**（`multilingual`）：實測測試檔大部分是英文演講、最後是國語主持人；強制 `zh` 會把英文直接翻成中文。對齊時依每段文字分組（有中文字用 zh 對齊模型，純英文用 en），各自對齊。
- **中文旁的半形標點轉全形**（`,` → `，` 等；連續的 `...` 當成刪節號轉成「……」），英文句子與數字不受影響。字典的誤聽也經過同一套轉換，寫半形標點也能命中。
- **ffmpeg／ffprobe 只允許讀本機檔案**（`-protocol_whitelist file,pipe`），並拒絕 hls、concat 等會去讀其他檔案或網址的格式（防內網 SSRF）。
- **聲紋 embedding 用 `pyannote/wespeaker-voxceleb-resnet34-LM`**（非 gated），登記樣本與轉錄中的講者用同一個模型算，確保在同一個向量空間。每位講者取最長的幾段語音、最多 30 秒，少於 2 秒不算聲紋。換了聲紋模型後舊聲紋不會被比對，聲紋庫頁面會提示重新登記。
- **重跑**沿用同一筆紀錄，完成後取代舊逐字稿（含編輯）。`batch_id` 保留不變。
- **YouTube 防 SSRF**：使用者貼的網址只用來解析出影片 ID（11 碼）或播放清單 ID，網域只接受 `youtube.com`（含 www、m、music）、`youtu.be`、`youtube-nocookie.com`，拒絕帳密、非 80／443 埠與其他協定；交給 yt-dlp 的一律是我們組出來的 `https://www.youtube.com/watch?v=<ID>`／`playlist?list=<ID>`，並加 `--use-extractors youtube`（播放清單 `youtube:tab`）與 `--ignore-config`。
- **yt-dlp 用子行程跑**（`python -m yt_dlp`，自己一個行程群組）：取消或逾時時連同它叫起來的 deno 一起砍掉，worker 不會被卡住；先 `-J` 取得資訊（順便檢查直播狀態、會員限定等），再用 `--load-info-json` 下載，不重新解析（下載那次一樣加 `--use-extractors youtube`：資訊檔失效時 yt-dlp 會改用網址重新解析）。下載逾時 = max(10 分鐘, 影片長度 × 2)；音訊大小上限沿用 `MAX_UPLOAD_MB`。
- **影片資訊快取**（`youtube_cache.py`，存完整資訊與取得時間，拿掉自動字幕、縮圖、分鏡圖這些大欄位，最多 300 筆）：API 30 分鐘內直接回傳；worker 下載時資訊在 **2 小時內**就直接沿用，不重新解析（播放清單的影片前端已逐支查過，不用查第二次）。YouTube 格式網址約 6 小時過期，萬一沿用的資訊下載失敗（HTTP 403／404／410、找不到格式等），worker 重新解析一次再試。查詢失敗（私人影片、被限流等）也快取 5 分鐘。API 同時最多 3 個 yt-dlp 查詢；`/api/youtube/resolve` 用專用的 3 條執行緒處理，一次貼 50 行時排隊的網址不會占用預設 executor（上傳與 SSE 照常）。
- **字幕與音訊分兩次下載**：先下載字幕，失敗（例如 HTTP 429）只在任務的警告寫「YouTube 人工字幕（…）下載失敗（HTTP 429，請求次數過多），已改用模型轉錄。」並改用模型轉錄，不會讓任務失敗；接著才下載音訊（進度條不會從 100% 跳回 5%）。重新轉錄時只缺字幕的情況也一樣。任務指定的模型沒安裝時，走字幕路線的任務照樣可以建立與處理，只有要改用模型轉錄時才失敗。
- **請求間隔**：worker 連 YouTube 時加 `--sleep-requests 1`（取得資訊、資訊失效時的重新解析）與 `--sleep-subtitles 1`（下載字幕前）。批次任務是一支接一支連續下載，同一台伺服器短時間內打很多請求容易被 YouTube 限流（429，最長約 1 小時），每個請求多等 1 秒（每支影片約多幾秒）換取較低的限流機率。API 的網址解析要即時回應，不加間隔，改用快取與同時 3 個的上限控制。
- **錯誤訊息**：yt-dlp 的錯誤對應成中文說明；「影片已下架」只比對 YouTube 對影片本身的說明，格式網址的 HTTP 404／410 另外歸類成「下載網址已過期」。沒有對應規則的錯誤顯示原始訊息時會遮掉伺服器上的絕對路徑（只留檔名）。
- **字幕文字**：拿掉 `<c>`、`<i>`、`<font>`、`<v>`、行內時間等標記，還原 `&amp;` 之類的實體；捲動式字幕重複的上一行、10 毫秒的快照 cue、連續相同的 cue 會合併——只有時間相接（下一個 cue 在上一個結束後 0.05 秒內開始；相同文字合併是 0.1 秒內）時才合併，間隔明顯的相同文字（例如兩次分開回答的「對」）保留成兩段；中文字之間的空白（YouTube 中文字幕習慣用空白當停頓）換成「，」（有逐字時間的 cue 也一樣）。SRT 的 cue 文字裡有空白行時整段保留。繁體字幕（zh-TW 等）不跑 OpenCC，原本的「台」「了解」不會被改寫（見上面「繁體轉換規則」）。
- **資料欄位**（`jobs`，舊資料庫啟動時自動補上，舊任務都是 `source='file'`）：`source`（file／youtube）、`source_url`、`video_id`、`yt_title`、`yt_channel`、`yt_language`、`yt_manual_captions`（人工字幕語言 JSON）、`use_captions`（有人工字幕時是否用字幕）、`caption_source`（`manual:<語言>` 或 `none`）、`download_pending`、`download_seconds`。YouTube 任務的 `filename` 是影片標題（首頁列表與佇列的顯示名稱）。`speaker_summary`：講者分布摘要（見下方「前端」），逐字稿存檔、編輯、還原時在同一個交易裡更新，只刪逐字稿時清空；欄位加上之前就完成的任務，第一次讀列表時從逐字稿補算一次。API 的任務另外多 `caption_lang`（依優先順序可用的字幕語言）。
- **匯出檔名**：YouTube 任務用影片標題（拿掉 `/`、`:` 等字元），JSON 匯出多了 `text_source` 與 `source`。

### 排隊順序

`jobs.position`（數字小的先處理；相同時依 `queued_at`、`id`）。worker 的 `claim_next` 在 `BEGIN IMMEDIATE` 交易裡領取 `position` 最小的排隊中任務。

| 情況 | 排到哪裡 |
|---|---|
| 新上傳（單檔或批次） | 佇列最後（排隊中最大的 position + 1；單一 INSERT 敘述，同時上傳不會拿到同一個順位） |
| 重新轉錄 | 佇列最後（同新上傳） |
| 容器重啟後放回佇列（上次處理到一半） | 佇列最前（排隊中最小的 position − 1），因為它原本就是正在處理的那一個 |
| 移到最前／移到最後／上移／下移／拖曳到某個任務前後 | `POST /api/jobs/{id}/move`：在 `BEGIN IMMEDIATE` 交易裡讀出目前順序、搬移、把排隊中任務重新編號為 1..n |

- 只有排隊中的任務能移動（處理中回 409「處理中的任務不能移動」）；拖曳的目標若剛好被 worker 領走也回 409。
- 處理中的任務不在排隊順序裡，其他任務不會排到它前面。
- 舊資料庫升級時（補 `position` 欄位）把排隊中／處理中的任務依原本的 `queued_at` 順序編為 1..n。

### 預估時間

`jobs/estimate.py`，所有數字在畫面上都標「約」：

- 每個任務 ≈ 固定開銷 2 秒 ＋ 速率 × 音檔秒數；佇列中換了模型（或第一個任務的模型還沒載入）時再加載入時間。
- 速率（每秒音檔要幾秒處理）依（模型, 是否講者辨識）分開算。預設值來自這台機器的 benchmark（一段 3016 秒的測試錄音：Breeze 轉錄約 172 秒、large-v3 約 180 秒、turbo 約 68 秒；講者辨識約 45 秒處理 2887 秒），再加 0.01 給轉檔、對齊與後處理。用 YouTube 人工字幕的任務歸在「captions」這一組（沒有轉錄，只有講者辨識＋其他），也不算 Whisper 載入時間。
- YouTube 還要下載的任務另外加下載時間 ≈ 8 秒（取得資訊、解 JS challenge）＋ 0.02 × 影片秒數（實測 2 分鐘影片約 2–11 秒），一樣用最近 20 筆實際下載秒數（`download_seconds`）以同樣的權重修正；`process_seconds` 不含下載時間。處理中的 YouTube 任務，進度條前面一段（依預估的下載／處理時間比例）給下載用。
- worker 完成任務時記下實際耗時：`process_seconds`（扣掉載入模型）與 `load_seconds`（`ModelManager` 以執行緒為單位累計載入秒數）。估算時用同組合最近 20 筆（音檔 ≥ 20 秒）修正：`速率 = (Σ(實際秒數 − 2) + 預設速率 × 600) / (Σ音檔秒數 + 600)`，也就是預設值相當於 10 分鐘音檔的權重，實測越多影響越小；載入時間用最近 10 次實際載入的平均（沒有紀錄時 15 秒）。
- 處理中：剩餘 ≈ 預估處理時間 × (1 − 進度)，辨識模型還在載入時再加載入時間。排隊中：等待 = 處理中剩餘 ＋ 前面每個任務的預估；完成 = 等待 ＋ 自己的預估。音檔長度不明的任務與排在它後面的任務顯示「無法估算」。
- API 的任務多了 `queue_rank`（第 N 位，1＝下一個）、`estimate_seconds`、`eta_start_seconds`、`eta_finish_seconds`、`estimate_basis`（`history`／`default`），取到 5 秒，經由現有的 SSE（`/api/events`、`/api/jobs/{id}/events`）推送。
- **播放檔**：上傳的原始檔（例如 `.qta`）瀏覽器不一定能播，轉錄時另外產生 AAC m4a（`+faststart`，支援拖曳跳播）。

## 部署

需求：GPU 主機已裝 Docker（nvidia runtime／CDI）、`docker compose` ≥ 2.24。

```bash
# 第一次：預先建置 Python 推論環境（torch＋whisperx＋pyannote，最久）
scripts/deploy.sh --base-only

# 本機設定：複製 .deploy.env.example 成 .deploy.env，填 ssh 主機名稱、服務網址、1Password 項目
cp .deploy.env.example .deploy.env

# HF token（pyannote gated 模型需要）：從 1Password 經 ssh stdin 寫進 GPU 主機的 .env，不會印出
# GPU 主機的 .env 另外要設 BIND_IP（服務綁定的內網 IP），範例見 .env.example
scripts/set-hf-token.sh

# 先看會更新、刪除哪些檔案（不同步）
scripts/deploy.sh --dry-run

# 同步程式碼並建置、啟動
scripts/deploy.sh
```

- `deploy.sh` 用 `rsync --delete` 同步程式碼到 GPU 主機的 `~/docker/voice-to-text/`：只送 `Dockerfile`、`docker-compose.yml`、`.dockerignore`、`.env.example`、`README.md`、`backend/`、`frontend/`、`scripts/`，本機刪掉的檔案 GPU 主機上也會刪掉。`data/`、`models/`、`.env`、`bench/`、`bench-data/` 用 rsync 的 protect 規則保護，**不會**被刪除或覆蓋。
- 每次都先預覽（`rsync -n --itemize-changes`）：有檔案要刪除時列出清單，輸入 `y` 才繼續（`DEPLOY_YES=1` 跳過確認）；一次最多刪 300 個檔案（`--max-delete`）。`DEPLOY_DIR` 只接受 `docker/` 底下的路徑。本機是 macOS 的 openrsync，只用它和 GPU 主機上的 rsync 3 都支援的選項。
- 部署前會先查線上 `/api/jobs`（`DEPLOY_URL` 可覆寫，預設 `http://127.0.0.1:8810`，建議在 `.deploy.env` 設定）：有排隊中／轉錄中的任務就列出並中止，不同步、不重建（重建會重啟容器、打斷轉錄）。要硬部署加 `--force`（或 `DEPLOY_FORCE=1`），`DEPLOY_YES=1` 不會略過這項檢查；`--dry-run` 只顯示不中止；連不上服務時只警告並繼續（服務壞掉時才能部署修正）。
- 網址：`http://<BIND_IP>:8810/`（也綁 `127.0.0.1:8810`），不設登入，只開內網 IP。
- Volume：`./data → /data`（SQLite、音檔、結果）、`./models → /models`（與 benchmark 共用：`HF_HOME=/models/hf`、`TORCH_HOME=/models/torch`、Breeze CTranslate2 在 `/models/ct2`）。
- 容器以 root 執行，因為 `models/` 由 benchmark 容器（root）建立，需要寫入同一份 HF 快取。

驗證：

```bash
curl http://<host>:8810/healthz   # {"status":"ok","worker":"alive"}；worker 執行緒死掉時回 503
curl http://<host>:8810/api/status
curl -F file=@clip.m4a -F diarize=true http://<host>:8810/api/jobs
curl -F file=@a.m4a -F batch_id=mybatch1 http://<host>:8810/api/jobs   # 批次：同一批帶同一個 batch_id（英數、-、_，最多 64 字）
curl -X POST -H 'Content-Type: application/json' -d '{"to":"front"}' http://<host>:8810/api/jobs/<id>/move
#   也可以 {"to":"back|up|down"}、{"before":"<另一個任務 id>"}、{"after":"<另一個任務 id>"}
curl -X POST http://<host>:8810/api/batches/<batch_id>/cancel
curl -N http://<host>:8810/api/jobs/<id>/events
# YouTube：解析網址（影片回傳資訊、播放清單回傳影片清單）→ 查單支影片 → 建立任務
curl -X POST -H 'Content-Type: application/json' -d '{"text":"https://youtu.be/<ID>\nhttps://www.youtube.com/playlist?list=<LIST>"}' http://<host>:8810/api/youtube/resolve
curl http://<host>:8810/api/youtube/videos/<ID>
curl -X POST -H 'Content-Type: application/json' -d '{"video_id":"<ID>","batch_id":"mybatch1"}' http://<host>:8810/api/jobs/youtube
curl -X POST -H 'Content-Type: application/json' -d '{"use_captions":false}' http://<host>:8810/api/jobs/<id>/rerun   # 改用模型轉錄
curl http://<host>:8810/api/jobs/<id>/export/srt
```

## 設定值（環境變數，可寫在 GPU 主機的 `.env`）

| 變數 | 預設 | 說明 |
|---|---|---|
| `HF_TOKEN` | （空） | HuggingFace read token；沒有時講者辨識會失敗但仍輸出逐字稿 |
| `DEFAULT_MODEL` | `breeze-asr-25` | 首頁預設模型（`breeze-asr-25`／`large-v3-turbo`／`large-v3`）。設定的模型沒安裝（Breeze 沒有 `model.bin`）時改用 large-v3 |
| `BREEZE_MODEL_PATH` | `/models/ct2/breeze-asr-25-f16` | Breeze-ASR-25 的 CTranslate2 目錄；有 `model.bin` 才會在 UI 啟用 |
| `ASR_LANGUAGE` | （空＝自動偵測） | 指定 Whisper 語言，例如 `zh`。指定後不做逐段偵測，英文段落會被翻成中文 |
| `ASR_MULTILINGUAL` | `1` | 自動偵測時每個 30 秒視窗各自偵測語言（中英段落交錯的錄音） |
| `ASR_INITIAL_PROMPT` | （空） | 額外的提示句，與字典詞彙一起放進 hotwords。注意 Whisper 可能在靜音處把提示句吐出來（已有過濾） |
| `ASR_BEAM_SIZE` | `5` | beam size |
| `ASR_COMPUTE_TYPE` | `float16` | GPU 計算精度（CPU 一律 `int8`） |
| `DIARIZATION_MODEL` | `pyannote/speaker-diarization-community-1` | pyannote 講者分段 pipeline |
| `EMBEDDING_MODEL` | `pyannote/wespeaker-voxceleb-resnet34-LM` | 聲紋 embedding 模型 |
| `VOICEPRINT_THRESHOLD` | `0.55` | 聲紋 cosine 相似度門檻 |
| `IDLE_UNLOAD_SECONDS` | `600` | 模型閒置多久卸載 |
| `MAX_UPLOAD_MB` | `2048` | 上傳大小上限 |

## 前端

React 19＋Vite＋TypeScript（`frontend/`）：逐字稿以頭像泡泡呈現，手機上有上拉式播放面板。

- **頁面**：`/` 錄音（新增、要上傳的清單、處理中與排隊中、已完成；`/history` 轉到這裡）、`/jobs/:id` 結果頁、`/dictionaries` 字典、`/voiceprints` 聲紋庫。導覽只有「錄音／字典／聲紋庫」，右側是外觀（跟隨系統／淺色／深色）。
- **元件**：
  - `components/`：`Modal`（對話框、`ConfirmModal`、`PromptModal`；背景 inert、焦點循環與還原）、`BottomSheet`（手機首頁的網址與設定）、`Menu`／`MenuButton`（錨點選單，↑↓、Esc）、`SpeakerBand`（結果頁色帶＋首頁 `MiniBand`）、`StepBar`（處理步驟五格細線）、`Tick`（方框文字切換）、`FileDropzone`（四角裁切記號）、`JobOptionsForm`、`YoutubeInput`（含播放清單勾選）、`JobDialogs`（三種刪除、重新轉錄）、`Toast`、`Icon`。
  - `pages/home/`：`useUploader`（批次上傳邏輯）、`UploadSection`、`QueueSection`、`DoneSection`。
  - `pages/result/`：`ResultView`（有逐字稿時的主體）、`Transcript`（頭像泡泡、原地編輯、長按／右鍵／「⋯」選單）、`BubbleMenu`、`DictSuggest`、`SpeakerList`、`PlayerBar`（桌機）、`PlayerPanel`（手機彈簧面板）、`media.tsx`（`AudioSource`、`YoutubePlayer`）。
  - `lib/player.ts`：**播放器狀態集中在一個 `PlayerStore`**，色帶、泡泡、播放條、播放面板、YouTube 播放器共用同一個時間來源；媒體元件 attach 後回報時間，畫面用 `usePlayer(selector)` 只訂閱需要的部分（泡泡只在段落換了時重畫，色帶細線直接改 DOM）。「只聽某位講者」也在這裡處理。
  - `lib/speakers.ts`（講者順序與配色、色帶合併）、`lib/spring.ts`（彈簧與動量投射）、`lib/steps.ts`（後端 stage → 五格步驟）、`lib/clipboard.ts`（http 下也能複製）、`theme.ts`（外觀）。
- **樣式**：`src/styles/tokens.css` 是全部的色彩、字體、間距 token（`light-dark()`，預設跟著系統，手動選的記在 localStorage `v2t.theme`，`index.html` 在第一次繪製前套用），`base.css`（共用元件）、`home.css`、`result.css`、`library.css`。鈷藍只代表「現在」（播放中、處理中、焦點），主要按鈕用墨色實心。講者色 5 個色相，第 6–10 位用同色相的淡色版。
- **講者順序與配色**：辨識出來的 `SPEAKER_xx` 依字母排序在前、手動新增的 `MANUAL_xx` 在後，索引就是顏色；後端 `speaker_summary` 用同樣的順序，所以首頁迷你色帶和結果頁顏色一致，新增講者時原本的顏色也不會變。
- **首頁迷你色帶**：列表 API（`GET /api/jobs`、SSE `/api/events`）的每筆任務多一個 `speaker_summary`：`{duration, speakers: [{label, name, seconds, share}], runs: [[開始, 結束, 講者索引]]}`。同講者間隔不到 1.5 秒的段落合併；超過 120 段時把時間軸切成 120 格、每格取講最多的人。前端不用另外載入逐字稿。
- **無障礙與防遺失**：未儲存的泡泡編輯、字典、上傳中離開頁面都會先確認（站內換頁用 `useBlocker`，關分頁用 `beforeunload`）；泡泡可以 Tab 到、Enter 播放、Shift＋F10 開選單；觸控目標 44px；`prefers-reduced-motion` 時色帶直接出現、播放面板改成淡入淡出、其他動畫關閉。
- **字型（自己架設，不連 Google Fonts）**：`@fontsource/noto-serif-tc`（400 逐字稿、600 標題）與 `@fontsource/noto-sans-tc`（400 介面、600 按鈕與講者名），5.3.1。每個字重依 unicode-range 切成約 108 個 woff2 小檔（每檔約 1–60 KB），瀏覽器只下載畫面上用到的字：實測結果頁約下載 30–50 個切片。四個字重的 woff2 合計約 10.3 MB（宋體每個字重約 2.9 MB、黑體約 2.2 MB），全部打包進 `dist/assets`（`vite.config.ts` 的 `woff2Only` 外掛拿掉了 woff 備援，dist 從約 24 MB 降到 12 MB）。`@font-face` 宣告約 460 KB（伺服器用 gzip 壓縮後約 195 KB；SSE、音檔、Range 請求、woff2 不壓縮，見 `main.py` 的 `CompressMiddleware`），放在獨立的 `fonts-*.css`，由 `main.tsx` 動態 import 非同步載入，不擋第一次繪製；載入前先用系統字（Songti TC／PingFang TC），之後換上（`font-display: swap`）。

## 開發與測試

```bash
# 後端單元測試（不需要 GPU；Mac 上用容器跑 Python 3.12）
docker run --rm -v "$PWD/backend":/src -w /src python:3.12-slim \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q tests"

# 也可以在部署好的容器裡跑
ssh <gpu-host> 'cd ~/docker/voice-to-text && docker compose exec -T -e TMPDIR=/tmp app python -m pytest -q -p no:cacheprovider tests'

# 前端
cd frontend && npm ci && npm run typecheck && npm run build
VITE_API=http://<BIND_IP>:8810 npm run dev   # 本機開發，API 轉到 GPU 主機
```

測試涵蓋：YouTube（網址解析與非 YouTube 網域被拒、字幕語言優先順序、自動字幕不採用、VTT／SRT 解析、講者套到字幕段落、字幕路線不呼叫 ASR／對齊、各種下載錯誤訊息、播放清單展開與建立任務／下載／重新轉錄／取消／刪除（mock yt-dlp）、yt-dlp 子行程取消與逾時、下載時間估算、migration）、字典解析／替換／加入規則、OpenCC＋字典、SRT／VTT 時間格式與匯出內容、逐字指派講者與切句（含缺時間戳、平滑、空白字元）、逐字稿編輯、三種刪除模式、佇列（排序、重新排隊、取消、依 position 領取、調整順序含並發與處理中不能移動、batch_id、整批取消、預估時間有／無歷史資料、資料庫 migration）、聲紋比對、以假後端跑完整推論流程（含 gated 失敗、OOM、取消）、API 端對端。

## YouTube 下載元件（yt-dlp）

`backend/requirements-youtube.txt`：`yt-dlp[default,deno,curl-cffi]==2026.10.26`（2026-11-18 時的最新正式版），在 Dockerfile 最後階段獨立一層安裝（改版本只重建這一層，不會重裝 torch）。

- **為什麼要 deno**：YouTube 的播放網址要先解 JavaScript challenge（n 參數、簽章），yt-dlp 從 2025.11 起改用外部 JS runtime 執行 [yt-dlp-ejs](https://github.com/yt-dlp/ejs) 的 solver 腳本，沒有 runtime 時大部分格式拿不到。yt-dlp 預設只啟用 deno（node／bun／quickjs 要另外指定），`[deno]` extra 從 PyPI 裝 deno 2.9.8 執行檔；`[default]` 內含 `yt-dlp-ejs` 0.8.0（solver 腳本，不用連 npm／GitHub 下載）。
- **curl-cffi**：YouTube 部分請求要求瀏覽器 TLS 指紋（impersonation），沒有時 yt-dlp 會警告並可能失敗。
- **PO Token**：目前不需要。預設的 player client（`tv_samsung`、`visionos`、`web`）下載音訊與人工字幕實測都不用 PO Token（`web` client 的字幕需要，但 yt-dlp 會改用其他 client）。如果之後 YouTube 擴大要求、開始出現「HTTP 403」「不是機器人」的錯誤，先更新 yt-dlp；仍不行再裝 PO Token provider 外掛（例如 `bgutil-ytdlp-pot-provider`）。
- **更新**：改 `requirements-youtube.txt` 的版本後 `scripts/deploy.sh`；`/api/status` 的 `ytdlp_version` 可以確認目前版本。
- 不支援需要登入的影片（私人、年齡限制、會員限定），不使用 cookies（帳號有被封鎖的風險）。

## 已知限制

- 同一張 GPU 可能與其他服務（例如 ComfyUI）共用，不做鎖協調。CUDA OOM 時先卸載自己快取中閒置的其他模型（保留這一步要用的）並重試該步驟一次，仍不足就讓任務失敗（「GPU 忙碌，請稍後重試」），需手動重新轉錄。聲紋登記（API）遇到 OOM 直接回 GPU 忙碌，不卸載 worker 的模型。
- 取消是在步驟之間或轉錄每個段落時檢查；pyannote 講者分段跑到一半無法中斷，要等這一步結束。
- 長錄音（例如 48 分鐘）講者分段在 CPU 記憶體與 GPU 上都較吃資源；pyannote 的 embedding 步驟時間與音檔長度成正比。
- WhisperX 中文對齊模型詞彙是簡體字，英文單字沒有對齊時間時用前後字內插，講者切換點在中英夾雜處可能偏差幾百毫秒。
- 聲紋登記走 API 請求同步計算（與 worker 共用模型快取），不經過佇列。
- 瀏覽器錄音登記聲紋需要 HTTPS，v1 只支援上傳檔案。
- YouTube：一次最多 50 個網址，播放清單最多列 200 支；直播中、剛結束還在處理的直播、首播前的影片不能下載；人工字幕的時間點是上傳者對的，講者辨識套用時以字幕段落為單位，一段字幕裡換人說話不會切開（除非字幕有逐字時間）。YouTube 改版可能讓下載暫時失敗，需要更新 yt-dlp。
- 只刪音檔後 YouTube 任務的結果頁仍可用嵌入的影片播放，但不能重新轉錄（不會自動重新下載）。
- 前端：手機的長按與上拉播放面板只在 Chrome 觸控模擬驗證過，沒有在實機 iPhone Safari 上測；手機的 YouTube 小視窗高度不到 200px（YouTube 建議的最小尺寸），目前可以正常播放。超過 10 位講者時顏色會循環。
- 不做 LLM 推斷講者姓名、去贅字、DOCX 匯出、登入（spec「v1 不做」）。

## 授權

[MIT](LICENSE)
