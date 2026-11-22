export type JobStatus = "queued" | "running" | "done" | "failed" | "canceled";

export interface Job {
  id: string;
  filename: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  status: JobStatus;
  /** 處理步驟的顯示文字（後端依語言 render） */
  stage: string;
  /** 處理步驟的代碼（例如 stage.transcribe），判斷目前在哪一步用；舊資料為 null */
  stage_code?: string | null;
  progress: number;
  error: string | null;
  warnings: string[];
  model: string;
  diarize: boolean;
  num_speakers: number | null;
  dictionary_ids: number[];
  has_audio: boolean;
  has_playback: boolean;
  has_transcript: boolean;
  duration: number | null;
  size_bytes: number;
  cancel_requested: boolean;
  /** 同一次批次上傳的任務共用；單檔上傳為 null */
  batch_id: string | null;
  /** 前面還有幾個任務（含處理中） */
  queue_position: number | null;
  /** 排隊順位，1＝下一個處理；處理中與已結束為 null */
  queue_rank: number | null;
  /** 這個任務本身的預估處理秒數（約） */
  estimate_seconds: number | null;
  /** 預估幾秒後開始處理（處理中為 0）；無法估算為 null */
  eta_start_seconds: number | null;
  /** 預估幾秒後完成；無法估算為 null */
  eta_finish_seconds: number | null;
  /** history：已用過去任務的實際耗時修正；default：只用預設速度 */
  estimate_basis: "history" | "default" | null;
  /** 來源：上傳的檔案或 YouTube 網址 */
  source: "file" | "youtube";
  source_url: string | null;
  video_id: string | null;
  yt_title: string | null;
  yt_channel: string | null;
  /** 這部影片有哪些人工字幕（語言代碼）；自動字幕不算 */
  yt_manual_captions: string[];
  /** 依優先順序可以用的人工字幕語言；沒有為 null（只能用模型轉錄） */
  caption_lang: string | null;
  /** 有人工字幕時是否直接用字幕 */
  use_captions: boolean;
  /** 這次逐字稿的文字來源：manual:<語言>＝YouTube 人工字幕、none＝模型轉錄；尚未處理為 null */
  caption_source: string | null;
  /** 講者分布摘要（首頁迷你色帶與占比）；沒有逐字稿為 null */
  speaker_summary: SpeakerSummary | null;
}

/** 後端 transcript.speaker_summary：講者依代號排序，索引就是講者色的順序。 */
export interface SpeakerSummary {
  duration: number;
  speakers: { label: string; name: string; seconds: number; share: number }[];
  /** [開始秒, 結束秒, 講者索引]，已合併相鄰同講者的段落 */
  runs: [number, number, number][];
}

/** /api/youtube/resolve 與 /api/youtube/videos/{id} 回傳的影片資訊 */
export interface YoutubeVideo {
  video_id: string;
  title: string;
  channel: string;
  duration: number | null;
  live_status: string | null;
  manual_captions: string[];
  /** 會採用的人工字幕語言；null＝用模型轉錄 */
  caption_lang: string | null;
  url: string;
  /** 無法處理的原因（例如直播尚未結束）；可以處理為 null */
  error: string | null;
}

export interface YoutubePlaylistEntry {
  video_id: string;
  title: string;
  duration: number | null;
  channel: string;
  unavailable: boolean;
  reason: string | null;
}

export interface YoutubePlaylist {
  playlist_id: string;
  title: string;
  channel: string;
  count: number;
  truncated: boolean;
  entries: YoutubePlaylistEntry[];
}

export type YoutubeResolveItem =
  | { input: string; kind: "invalid"; error: string }
  | { input: string; kind: "video"; video: YoutubeVideo; error?: undefined }
  | { input: string; kind: "video"; video_id: string; error: string; video?: undefined }
  | { input: string; kind: "playlist"; playlist: YoutubePlaylist; video_id: string | null; error?: undefined }
  | { input: string; kind: "playlist"; playlist_id: string; error: string; playlist?: undefined };

export type MoveTarget = { to: "front" | "back" | "up" | "down" } | { before: string } | { after: string };

export interface MoveResult {
  order: string[];
  jobs: Job[];
}

export interface BatchCancelResult {
  canceled: number;
  canceling: number;
  total: number;
  jobs: Job[];
}

export interface Segment {
  id: number;
  start: number;
  end: number;
  speaker: string | null;
  text: string;
}

export interface SpeakerInfo {
  name: string | null;
  voiceprint_id: number | null;
  similarity: number | null;
}

export interface Transcript {
  language: string;
  duration: number;
  /** 走 YouTube 人工字幕路線時為 null */
  model: string | null;
  text_source?: { type: "model" | "youtube_captions"; lang?: string | null; model?: string; label: string };
  diarization: {
    enabled: boolean;
    ok: boolean;
    error: string | null;
    num_speakers: number | null;
    detected_speakers?: number;
  };
  speakers: Record<string, SpeakerInfo>;
  segments: Segment[];
  warnings: string[];
}

export interface TranscriptPayload {
  original: Transcript;
  current: Transcript;
  edited: boolean;
  voiceprint_speakers: string[];
}

export interface Dictionary {
  id: number;
  name: string;
  content: string;
  term_count: number;
  rule_count: number;
  created_at: number;
  updated_at: number;
}

export interface Voiceprint {
  id: number;
  name: string;
  embedding_model: string;
  source: string;
  duration: number | null;
  created_at: number;
  compatible: boolean;
}

export interface ModelInfo {
  id: string;
  label: string;
  description: string;
  available: boolean;
}

export interface ModelList {
  default: string;
  max_upload_mb?: number;
  models: ModelInfo[];
}

export interface JobOptionsValue {
  model: string;
  diarize: boolean;
  numSpeakers: number | null; // null = 自動
  dictionaryIds: number[];
}

export type EditOp =
  | { op: "rename_speaker"; speaker: string; name: string }
  | { op: "set_text"; segment_id: number; text: string }
  | { op: "set_speaker"; segment_id: number; speaker: string | null; new_name?: string }
  | { op: "merge_speakers"; source: string; target: string };
