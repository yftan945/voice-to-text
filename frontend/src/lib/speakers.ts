import { tr } from "../i18n";
import type { Segment, Transcript } from "../types";

/** 講者色：5 個色相（tokens.css 的 --sp0..4），第 6–10 位用同色相的淡色版（.sp5..9），再多就循環。 */
export const SPEAKER_COLORS = 10;

/**
 * 講者代號的順序（索引就是配色）：辨識出來的 SPEAKER_xx 依字母排序在前，手動新增的 MANUAL_xx 在後，
 * 新增講者時原本的講者顏色不會變。後端 speaker_summary 用同樣的順序，首頁迷你色帶與結果頁顏色一致。
 */
export function speakerLabels(t: Transcript): string[] {
  const manual = (l: string) => (l.startsWith("MANUAL_") ? 1 : 0);
  return Object.keys(t.speakers).sort((a, b) => manual(a) - manual(b) || (a < b ? -1 : a > b ? 1 : 0));
}

export function colorClass(index: number): string {
  return index < 0 ? "spx" : `sp${index % SPEAKER_COLORS}`;
}

const AUTO_LABEL = /^SPEAKER_(\d+)$/;

/**
 * 沒取名的講者顯示的名字：辨識出來的 SPEAKER_xx 一律顯示成「講者 N」（N＝代號數字＋1，和頭像上的數字一致）；
 * 手動新增的 MANUAL_xx 照原樣。與後端 transcript.default_speaker_name() 一致（匯出檔案也用同樣的名字）。
 */
export function defaultSpeakerName(label: string): string {
  const m = AUTO_LABEL.exec(label);
  return m ? tr("speaker.default", { n: Number(m[1]) + 1 }) : label;
}

/** 講者顯示名稱：有取名用名字，沒有就是「講者 N」。name 是舊資料（首頁摘要）存的代號時也一樣換掉。 */
export function displaySpeaker(label: string, name: string | null | undefined): string {
  const n = (name ?? "").trim();
  return n && !AUTO_LABEL.test(n) ? n : defaultSpeakerName(label);
}

export function speakerName(t: Transcript, label: string | null): string {
  if (!label) return tr("speaker.none");
  return displaySpeaker(label, t.speakers[label]?.name);
}

/**
 * 頭像上的字：有名字取第一個字（英文大寫）；還是代號時用數字：SPEAKER_xx 用代號數字＋1（和「講者 N」一致），
 * 其他（MANUAL_xx）用排序位置＋1。
 */
export function avatarChar(name: string | null | undefined, index: number): string {
  const n = (name ?? "").trim();
  const m = AUTO_LABEL.exec(n);
  if (m) return String(Number(m[1]) + 1);
  if (!n || /^MANUAL_\d+$/.test(n)) return String(index + 1);
  return [...n][0].toUpperCase();
}

export interface Run {
  s: number;
  e: number;
  /** 講者索引（-1＝沒有講者） */
  sp: number;
}

/** 相鄰、同講者、間隔小於 gap 秒的段落併成一段，色帶才不會碎成條碼。 */
export function mergeRuns(segs: Run[], gap = 1.5): Run[] {
  const out: Run[] = [];
  for (const g of segs) {
    const last = out[out.length - 1];
    if (last && last.sp === g.sp && g.s - last.e < gap) last.e = Math.max(last.e, g.e);
    else out.push({ ...g });
  }
  return out;
}

/** 播放時間所在的段落（二分搜尋）；段落結束後 1.2 秒內仍算這一段，之後算空檔（-1）。 */
export function segmentIndexAt(segments: Segment[], t: number): number {
  let lo = 0;
  let hi = segments.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].start <= t + 0.05) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (ans >= 0 && t > segments[ans].end + 1.2) return -1;
  return ans;
}

export interface SpeakerStat {
  count: number;
  seconds: number;
  /** 占全部說話時間的百分比（整數） */
  pct: number;
}

export function speakerStats(t: Transcript): Record<string, SpeakerStat> {
  const out: Record<string, SpeakerStat> = {};
  for (const l of Object.keys(t.speakers)) out[l] = { count: 0, seconds: 0, pct: 0 };
  let total = 0;
  for (const s of t.segments) {
    if (!s.speaker || !out[s.speaker]) continue;
    const d = Math.max(0, s.end - s.start);
    out[s.speaker].count += 1;
    out[s.speaker].seconds += d;
    total += d;
  }
  for (const l of Object.keys(out)) out[l].pct = total ? Math.round((out[l].seconds / total) * 100) : 0;
  return out;
}
