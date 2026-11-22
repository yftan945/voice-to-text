import type { Job } from "../types";

/** 處理步驟（對應後端 pipeline/runner.py 的 STAGES／CAPTION_STAGES 與 YouTube 下載）。 */
interface Step {
  name: string;
  /** 後端 stage 文字的開頭 */
  match: string[];
  /** 這一步在整體進度裡的區間（YouTube 任務前面還有一段下載，比例不固定，所以不算） */
  range?: [number, number];
}

const DOWNLOAD: Step = { name: "下載", match: ["取得影片資訊", "下載音訊", "下載字幕"] };

function stepsFor(job: Job): Step[] {
  const captions = job.source === "youtube" && job.use_captions && !!job.caption_lang;
  const out: Step[] = job.source === "youtube" ? [DOWNLOAD] : [];
  if (captions) {
    out.push({ name: "轉檔", match: ["轉檔"], range: [0, 0.1] });
    out.push({ name: "字幕", match: ["讀取 YouTube 字幕"], range: [0.1, 0.15] });
    if (job.diarize) out.push({ name: "講者辨識", match: ["講者辨識", "聲紋比對"], range: [0.15, 0.95] });
    out.push({ name: "後處理", match: ["整理結果"], range: [0.95, 1] });
    return out;
  }
  out.push({ name: "轉檔", match: ["轉檔"], range: [0, 0.05] });
  out.push({ name: "轉錄", match: ["語音轉文字", "載入"], range: [0.05, 0.6] });
  out.push({ name: "對齊", match: ["逐字對齊"], range: [0.6, 0.66] });
  if (job.diarize) out.push({ name: "講者辨識", match: ["講者辨識", "聲紋比對"], range: [0.66, 0.96] });
  out.push({ name: "後處理", match: ["整理結果"], range: [0.96, 1] });
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
  const names = steps.map((s) => s.name);
  if (job.status !== "running") return { names, current: -1, fraction: null };
  const current = steps.findIndex((s) => s.match.some((m) => job.stage.startsWith(m)));
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
