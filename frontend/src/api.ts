import type {
  BatchCancelResult,
  Dictionary,
  EditOp,
  Job,
  JobOptionsValue,
  ModelList,
  MoveResult,
  MoveTarget,
  TranscriptPayload,
  Voiceprint,
  YoutubeResolveItem,
  YoutubeVideo,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return "參數錯誤";
  }
  return `伺服器錯誤（${status}）`;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError(0, "連不上伺服器，請確認網路");
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new ApiError(res.status, detailMessage(res.status, data));
  return data as T;
}

function rerunBody(o: JobOptionsValue, useCaptions?: boolean) {
  return {
    model: o.model,
    diarize: o.diarize,
    num_speakers: o.diarize ? o.numSpeakers : null,
    dictionary_ids: o.dictionaryIds,
    use_captions: useCaptions ?? null,
  };
}

export const api = {
  models: () => request<ModelList>("GET", "/api/models"),

  jobs: () => request<Job[]>("GET", "/api/jobs"),
  job: (id: string) => request<Job>("GET", `/api/jobs/${id}`),
  cancel: (id: string) => request<Job>("POST", `/api/jobs/${id}/cancel`),
  /** 調整排隊順序（只有排隊中的任務可以移動）；回傳調整後的佇列。 */
  moveJob: (id: string, target: MoveTarget) => request<MoveResult>("POST", `/api/jobs/${id}/move`, target),
  cancelBatch: (batchId: string) =>
    request<BatchCancelResult>("POST", `/api/batches/${encodeURIComponent(batchId)}/cancel`),
  /** 重新轉錄；YouTube 來源可以指定 useCaptions（true＝用人工字幕、false＝用模型轉錄）。 */
  rerun: (id: string, o: JobOptionsValue, useCaptions?: boolean) =>
    request<Job>("POST", `/api/jobs/${id}/rerun`, rerunBody(o, useCaptions)),
  /**
   * 修改排隊中任務的設定（模型、講者、人數、字典；YouTube 可指定 useCaptions）；回傳更新後的任務。
   * 409＝任務已經開始處理或已結束，400＝設定不合法（訊息可直接顯示）。
   */
  updateOptions: (id: string, o: JobOptionsValue, useCaptions?: boolean) =>
    request<Job>("PATCH", `/api/jobs/${id}/options`, rerunBody(o, useCaptions)),

  /** 解析貼上的 YouTube 網址（一行一個）：影片回傳資訊，播放清單回傳影片清單。 */
  resolveYoutube: (text: string) =>
    request<{ items: YoutubeResolveItem[]; truncated: boolean }>("POST", "/api/youtube/resolve", { text }),
  youtubeVideo: (videoId: string) => request<YoutubeVideo>("GET", `/api/youtube/videos/${encodeURIComponent(videoId)}`),
  /** 建立 YouTube 任務（音訊由伺服器下載，不用上傳）。 */
  createYoutubeJob: (videoId: string, o: JobOptionsValue, batchId?: string) =>
    request<Job>("POST", "/api/jobs/youtube", {
      video_id: videoId,
      model: o.model,
      diarize: o.diarize,
      num_speakers: o.diarize ? o.numSpeakers : null,
      dictionary_ids: o.dictionaryIds,
      batch_id: batchId ?? null,
    }),
  deleteJob: (id: string, mode: "transcript" | "audio" | "all") =>
    request<{ deleted: string; job: Job | null }>("DELETE", `/api/jobs/${id}?mode=${mode}`),

  transcript: (id: string) => request<TranscriptPayload>("GET", `/api/jobs/${id}/transcript`),
  edit: (id: string, op: EditOp) => request<TranscriptPayload>("POST", `/api/jobs/${id}/transcript/edit`, op),
  resetEdits: (id: string) => request<TranscriptPayload>("POST", `/api/jobs/${id}/transcript/reset`),
  /** 存成聲紋；給 voiceprintId 時更新那筆聲紋（講者已比對到聲紋庫的人），不新增重複的一筆。 */
  saveSpeakerVoiceprint: (id: string, label: string, name: string, voiceprintId: number | null = null) =>
    request<TranscriptPayload & { voiceprint_updated?: boolean }>(
      "POST",
      `/api/jobs/${id}/speakers/${encodeURIComponent(label)}/voiceprint`,
      { name, voiceprint_id: voiceprintId },
    ),

  dictionaries: () => request<Dictionary[]>("GET", "/api/dictionaries"),
  createDictionary: (name: string, content: string) =>
    request<Dictionary>("POST", "/api/dictionaries", { name, content }),
  updateDictionary: (id: number, patch: { name?: string; content?: string }) =>
    request<Dictionary>("PATCH", `/api/dictionaries/${id}`, patch),
  deleteDictionary: (id: number) => request<unknown>("DELETE", `/api/dictionaries/${id}`),
  addRule: (id: number, wrong: string, correct: string) =>
    request<Dictionary>("POST", `/api/dictionaries/${id}/rules`, { wrong, correct }),

  voiceprints: () => request<Voiceprint[]>("GET", "/api/voiceprints"),
  enrollVoiceprint: (name: string, file: File) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("file", file);
    return request<Voiceprint>("POST", "/api/voiceprints", fd);
  },
  renameVoiceprint: (id: number, name: string) => request<unknown>("PATCH", `/api/voiceprints/${id}`, { name }),
  deleteVoiceprint: (id: number) => request<unknown>("DELETE", `/api/voiceprints/${id}`),
};

let modelsCache: Promise<ModelList> | null = null;

/** 模型清單只請求一次（失敗時下次再試）。 */
export function loadModels(): Promise<ModelList> {
  if (!modelsCache) {
    modelsCache = api.models().catch((e) => {
      modelsCache = null;
      throw e;
    });
  }
  return modelsCache;
}

/** 批次代號：同一批上傳的任務共用。http（非安全連線）下沒有 crypto.randomUUID，改用 getRandomValues。 */
export function newBatchId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** 上傳音檔（用 XHR 才能顯示上傳進度）。onProgress 的 loaded／total 是 bytes。 */
export function uploadJob(
  file: File,
  o: JobOptionsValue,
  onProgress: (fraction: number, loaded: number, total: number) => void,
  batchId?: string,
): { promise: Promise<Job>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Job>((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("model", o.model);
    fd.append("diarize", String(o.diarize));
    if (o.diarize && o.numSpeakers) fd.append("num_speakers", String(o.numSpeakers));
    fd.append("dictionary_ids", o.dictionaryIds.join(","));
    if (batchId) fd.append("batch_id", batchId);
    xhr.open("POST", "/api/jobs");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total, e.loaded, e.total);
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* 非 JSON 回應 */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as Job);
      else reject(new ApiError(xhr.status, detailMessage(xhr.status, data)));
    };
    xhr.onerror = () => reject(new ApiError(0, "上傳失敗，請確認網路"));
    xhr.onabort = () => reject(new ApiError(0, "已取消上傳"));
    xhr.send(fd);
  });
  return { promise, abort: () => xhr.abort() };
}

export const exportUrl = (id: string, fmt: string, timestamps = true) =>
  `/api/jobs/${id}/export/${fmt}?timestamps=${timestamps ? "true" : "false"}`;
export const audioUrl = (id: string) => `/api/jobs/${id}/audio`;
