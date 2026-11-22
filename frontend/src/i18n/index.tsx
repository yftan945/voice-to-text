import { createContext, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import en from "./en";
import zhTW, { type Messages } from "./zh-TW";

/**
 * 輕量 i18n（不用套件）：介面語言只有繁體中文（zh-TW）與英文（en）。
 * - 字典：zh-TW.ts 是基準，en.ts 用同一個型別（Messages），漏翻或多出 key 都會 typecheck 失敗。
 *   值是字串，或接收參數物件的函式（要插入數字、名稱，或英文要分單複數時）。
 * - 元件裡用 useT() 拿 t(key, params?)；語言改變時用到 useT／useLang 的元件會重畫。
 * - 不在元件裡的函式（format.ts、speakers.ts、api.ts…）用 tr()，讀目前語言（呼叫它們的元件會因為 useT 重畫）。
 * - 語言：第一次跟隨瀏覽器（navigator.languages 任何一個以 zh 開頭→zh-TW，否則 en）；
 *   手動選的記在 localStorage v2t.lang（index.html 在第一次繪製前先設好 <html lang>）。
 */

export type Lang = "zh-TW" | "en";
export type MsgKey = keyof Messages;
/** 值是字串（不帶參數）的 key：動態挑 key 的表（導覽、匯出格式、步驟名稱…）用這個，誤放帶參數的 key 會 typecheck 失敗。 */
export type StrKey = { [K in MsgKey]: Messages[K] extends string ? K : never }[MsgKey];
type Params<K extends MsgKey> = Messages[K] extends (p: infer P) => string ? P : never;
// key 是聯集時：全部是字串 key 才可以不帶參數；混了帶參數的 key 會要求 never 參數而 typecheck 失敗
type Args<K extends MsgKey> = [K] extends [StrKey] ? [] : [params: Params<K>];
export type TFn = <K extends MsgKey>(key: K, ...args: Args<K>) => string;

const DICTS: Record<Lang, Messages> = { "zh-TW": zhTW as Messages, en };
const KEY = "v2t.lang";

/** <html lang> 的值 */
export const HTML_LANG: Record<Lang, string> = { "zh-TW": "zh-Hant-TW", en: "en" };
/** Intl（日期、數字）用的 locale */
export const LOCALE: Record<Lang, string> = { "zh-TW": "zh-TW", en: "en-US" };

function stored(): Lang | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "zh-TW" || v === "en" ? v : null;
  } catch {
    return null;
  }
}

function detect(): Lang {
  const s = stored();
  if (s) return s;
  const list = typeof navigator === "undefined" ? [] : navigator.languages?.length ? navigator.languages : [navigator.language];
  return list.some((l) => typeof l === "string" && l.toLowerCase().startsWith("zh")) ? "zh-TW" : "en";
}

function apply(lang: Lang) {
  document.documentElement.lang = HTML_LANG[lang];
}

// 整個頁面共用一份語言狀態（同 theme.ts 的做法）
let current: Lang = detect();
const listeners = new Set<() => void>();
if (typeof document !== "undefined") apply(current);

function setCurrent(l: Lang) {
  if (l === current) return;
  current = l;
  apply(l);
  for (const fn of listeners) fn();
}

// 其他分頁改了語言時同步（沒有手動選過時，回到跟隨瀏覽器）
function onStorage(e: StorageEvent) {
  if (e.key === KEY || e.key === null) setCurrent(detect());
}

function subscribe(fn: () => void) {
  if (listeners.size === 0) window.addEventListener("storage", onStorage);
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) window.removeEventListener("storage", onStorage);
  };
}

/** 目前的介面語言（給不在元件裡的函式用）。 */
export const getLang = (): Lang => current;

/** 手動切換語言：記在 localStorage（寫不進去就只套用這一次）。 */
export function setLang(l: Lang) {
  try {
    localStorage.setItem(KEY, l);
  } catch {
    /* 無痕模式寫不進去就只套用這一次 */
  }
  setCurrent(l);
}

function translate(lang: Lang, key: MsgKey, params?: unknown): string {
  const v = DICTS[lang][key] as string | ((p: unknown) => string) | undefined;
  if (typeof v === "function") return v(params);
  return v ?? String(key);
}

const makeT = (lang: Lang): TFn => ((key: MsgKey, params?: unknown) => translate(lang, key, params)) as TFn;

/** 不在元件裡用的翻譯（讀目前語言）。 */
export const tr: TFn = ((key: MsgKey, params?: unknown) => translate(current, key, params)) as TFn;

/** 字典裡有沒有這個字串 key（動態組出來的 key，例如語言名稱、模型名稱，找不到時退回原文）；帶參數的 key 不算。 */
export function hasMsg(key: string): key is StrKey {
  return Object.prototype.hasOwnProperty.call(DICTS[current], key) && typeof DICTS[current][key as MsgKey] === "string";
}

/**
 * 存在 state 裡、換語言時要跟著換的訊息：i18n key（＋參數），或後端、瀏覽器回傳的原文（不翻譯）。
 * 用 msg()／textMsg() 建立，顯示時才用 showMsg() 翻成目前語言。
 */
export type Msg = { readonly key: MsgKey; readonly params?: unknown } | { readonly text: string };
export const msg = <K extends MsgKey>(key: K, ...args: Args<K>): Msg => ({ key, params: args[0] });
export const textMsg = (text: string): Msg => ({ text });
export const showMsg = (t: TFn, m: Msg): string =>
  "text" in m ? m.text : (t as (key: MsgKey, params?: unknown) => string)(m.key, m.params);

interface I18nValue {
  lang: Lang;
  t: TFn;
}

const I18nContext = createContext<I18nValue>({ lang: current, t: makeT(current) });

/** 提供語言與 t()；語言狀態本身在模組裡（setLang 不用透過 context）。 */
export function I18nProvider({ children }: { children: ReactNode }) {
  const lang = useSyncExternalStore(subscribe, getLang);
  const value = useMemo(() => ({ lang, t: makeT(lang) }), [lang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** 翻譯函式 t(key, params?)。 */
export const useT = (): TFn => useContext(I18nContext).t;

/** 目前的介面語言。 */
export const useLang = (): Lang => useContext(I18nContext).lang;
