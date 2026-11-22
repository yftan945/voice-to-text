/** 時間碼：m:ss，超過一小時 h:mm:ss（用 Sans 的 tabular-nums 顯示）。 */
export function tc(seconds: number): string {
  const t = Math.max(0, Math.floor(seconds));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h} 小時 ${m} 分`;
  if (m) return `${m} 分 ${sec} 秒`;
  return `${sec} 秒`;
}

/** 預估時間（一律標「約」）：不到 1 分鐘、約 N 分鐘、約 H 小時 M 分。 */
export function approxDuration(seconds: number): string {
  if (seconds < 60) return "不到 1 分鐘";
  const m = Math.ceil(seconds / 60);
  if (m < 60) return `約 ${m} 分鐘`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `約 ${h} 小時 ${rest} 分` : `約 ${h} 小時`;
}

/** 幾秒後的時鐘時間（HH:MM），跨日時加上日期。 */
export function clockAfter(seconds: number, now = Date.now()): string {
  const d = new Date(now + seconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return d.toDateString() === new Date(now).toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

/** 佇列進度的預估文字（處理中：何時完成；排隊中：何時開始、何時完成）。 */
export function etaText(job: {
  status: string;
  eta_start_seconds: number | null;
  eta_finish_seconds: number | null;
}): string {
  const finish = job.eta_finish_seconds;
  if (job.status === "running") {
    if (finish == null) return "無法估算剩餘時間";
    if (finish < 60) return "即將完成";
    return `${approxDuration(finish)}後完成（${clockAfter(finish)} 左右）`;
  }
  const start = job.eta_start_seconds;
  if (start == null || finish == null) return "無法估算等待時間（有檔案長度不明）";
  const head = start < 60 ? "即將開始處理" : `${approxDuration(start)}後開始`;
  return `${head}，${approxDuration(finish)}後完成`;
}

export function timeOfDay(ts: number): string {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function dateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** 找出改字的範圍（去掉相同的前後綴），給「加入字典」預填用。 */
export function diffSpan(oldText: string, newText: string): { wrong: string; correct: string } {
  let p = 0;
  while (p < oldText.length && p < newText.length && oldText[p] === newText[p]) p++;
  let s = 0;
  while (
    s < oldText.length - p &&
    s < newText.length - p &&
    oldText[oldText.length - 1 - s] === newText[newText.length - 1 - s]
  )
    s++;
  // 英文單字被切一半時往外擴到完整單字
  const isWord = (c: string | undefined) => !!c && /[A-Za-z0-9]/.test(c);
  while (p > 0 && isWord(oldText[p - 1]) && (isWord(oldText[p]) || isWord(newText[p]))) p--;
  while (s > 0 && isWord(oldText[oldText.length - s]) && isWord(oldText[oldText.length - s - 1])) s--;
  // 中文改一個字時，單字當規則容易誤換：往後（不行就往前）擴到至少兩個字
  const isCjk = (c: string | undefined) => !!c && /[\u3400-\u9fff\uf900-\ufaff]/.test(c);
  while (oldText.length - s - p < 2 && (s > 0 || p > 0)) {
    if (s > 0 && isCjk(oldText[oldText.length - s])) s--;
    else if (p > 0 && isCjk(oldText[p - 1])) p--;
    else break;
  }
  return {
    wrong: oldText.slice(p, oldText.length - s).trim(),
    correct: newText.slice(p, newText.length - s).trim(),
  };
}

const CJK = /[\u3400-\u9fff\uf900-\ufaff]/;

/** 同一位講者的兩段之間停頓超過這個秒數就換行（與後端 TXT 匯出一致）。 */
export const PARAGRAPH_GAP = 2;

/**
 * 兩段文字接起來時中間要補什麼（與後端 export._separator 一致）：英文與英文之間補空格；
 * 中文接英文或數字、英文或數字接中文時補空格；中文接中文、前一段沒有句末標點時補「，」。
 */
export function joinSeparator(prev: string, next: string): string {
  const last = prev.slice(-1);
  const first = next.slice(0, 1);
  if (!last || !first) return "";
  const alnum = /[A-Za-z0-9]/;
  if (/[\x21-\x7e]/.test(last) && alnum.test(first)) return " ";
  if ((CJK.test(last) && alnum.test(first)) || (alnum.test(last) && CJK.test(first))) return " ";
  if (CJK.test(last) && CJK.test(first)) return "，";
  return "";
}

const LANGUAGE_NAMES: Record<string, string> = {
  "zh-tw": "台灣繁體中文",
  "zh-hant": "繁體中文",
  "zh-hk": "香港繁體中文",
  "zh-mo": "澳門繁體中文",
  zh: "中文",
  "zh-hans": "簡體中文",
  "zh-cn": "簡體中文",
  "zh-sg": "簡體中文（新加坡）",
  en: "英文",
  ja: "日文",
  ko: "韓文",
  fr: "法文",
  de: "德文",
  es: "西班牙文",
  vi: "越南文",
  th: "泰文",
  id: "印尼文",
  ms: "馬來文",
  nan: "台語",
  hak: "客語",
};

/** 字幕語言代碼 → 顯示名稱（與後端 youtube.language_name 一致）。 */
export function languageName(code: string | null | undefined): string {
  if (!code) return "";
  const key = code.toLowerCase();
  return LANGUAGE_NAMES[key] ?? LANGUAGE_NAMES[key.split("-")[0]] ?? code;
}

/** caption_source（manual:zh-TW）→ 使用的字幕語言；不是用字幕回傳 null。 */
export function captionSourceLang(source: string | null | undefined): string | null {
  return source?.startsWith("manual:") ? source.slice("manual:".length) : null;
}
