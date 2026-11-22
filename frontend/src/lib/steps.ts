import { tr, type StrKey } from "../i18n";
import type { Job } from "../types";

/** 處理步驟（對應後端 pipeline/runner.py 的 STAGES／CAPTION_STAGES 與 YouTube 下載）。 */
interface Step {
  /** 步驟名稱（i18n 字典的 key） */
  name: StrKey;
  /** 後端 stage_code（例如 stage.transcribe）屬於這一步的代碼 */
  codes: string[];
  /**
   * 沒有 stage_code 的舊資料：比對 stage 文字的開頭（不分大小寫，中文與英文都列）；
   * 都對不上時非 YouTube 任務改用進度區間判斷（見 jobSteps）。
   */
  match: string[];
  /** 這一步在整體進度裡的區間（YouTube 任務前面還有一段下載，比例不固定，所以不算） */
  range?: [number, number];
}

const M = {
  download: ["取得影片資訊", "下載音訊", "下載字幕", "fetching video", "getting video", "downloading"],
  convert: ["轉檔", "convert"],
  captions: ["讀取 YouTube 字幕", "reading youtube captions", "reading captions", "loading captions"],
  transcribe: ["語音轉文字", "載入", "transcrib", "speech to text", "loading"],
  align: ["逐字對齊", "align"],
  diarize: ["講者辨識", "聲紋比對", "identifying speakers", "speaker", "diariz", "matching voiceprints", "voiceprint"],
  post: ["整理結果", "finaliz", "post-process", "postprocess", "wrapping up"],
};

/** stage_code → 步驟 */
const C = {
  download: ["stage.fetching_info", "stage.downloading_captions", "stage.downloading_audio"],
  convert: ["stage.convert"],
  captions: ["stage.captions"],
  transcribe: ["stage.transcribe"],
  align: ["stage.align"],
  diarize: ["stage.diarize", "stage.voiceprint"],
  post: ["stage.postprocess"],
};

const DOWNLOAD: Step = { name: "step.download", codes: C.download, match: M.download };

function stepsFor(job: Job): Step[] {
  const captions = job.source === "youtube" && job.use_captions && !!job.caption_lang;
  const out: Step[] = job.source === "youtube" ? [DOWNLOAD] : [];
  if (captions) {
    out.push({ name: "step.convert", codes: C.convert, match: M.convert, range: [0, 0.1] });
    out.push({ name: "step.captions", codes: C.captions, match: M.captions, range: [0.1, 0.15] });
    if (job.diarize) out.push({ name: "step.diarize", codes: C.diarize, match: M.diarize, range: [0.15, 0.95] });
    out.push({ name: "step.post", codes: C.post, match: M.post, range: [0.95, 1] });
    return out;
  }
  out.push({ name: "step.convert", codes: C.convert, match: M.convert, range: [0, 0.05] });
  out.push({ name: "step.transcribe", codes: C.transcribe, match: M.transcribe, range: [0.05, 0.6] });
  out.push({ name: "step.align", codes: C.align, match: M.align, range: [0.6, 0.66] });
  if (job.diarize) out.push({ name: "step.diarize", codes: C.diarize, match: M.diarize, range: [0.66, 0.96] });
  out.push({ name: "step.post", codes: C.post, match: M.post, range: [0.96, 1] });
  return out;
}

export interface StepState {
  names: string[];
  /** 目前在第幾步（-1＝還沒開始或認不出來） */
  current: number;
  /** 目前這一步完成多少（0–1）；算不出來（YouTube 下載後的步驟）為 null */
  fraction: number | null;
}

export function jobSteps(job: Job): StepState {
  const steps = stepsFor(job);
  const names = steps.map((s) => tr(s.name));
  if (job.status !== "running") return { names, current: -1, fraction: null };
  const code = job.stage_code;
  const stage = job.stage.toLowerCase();
  // 有 stage_code 就看代碼（不受語言影響）；舊資料（null）才比對 stage 文字
  let current = code
    ? steps.findIndex((s) => s.codes.includes(code))
    : steps.findIndex((s) => s.match.some((m) => stage.startsWith(m.toLowerCase())));
  // 認不出來（例如 stage.preparing、舊資料的寫法不同）：上傳的檔案用進度落在哪個區間判斷
  if (current < 0 && job.source !== "youtube")
    current = steps.findIndex((s) => !!s.range && job.progress >= s.range[0] && job.progress < s.range[1]);
  if (current < 0) return { names, current, fraction: null };
  const step = steps[current];
  if (step === DOWNLOAD) {
    const m = job.stage.match(/(\d+)%/);
    return { names, current, fraction: m ? Number(m[1]) / 100 : null };
  }
  if (job.source === "youtube" || !step.range) return { names, current, fraction: null };
  const [lo, hi] = step.range;
  return { names, current, fraction: Math.max(0, Math.min(1, (job.progress - lo) / (hi - lo))) };
}
