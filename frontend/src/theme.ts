import { useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "v2t.theme";

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function apply(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

/** 外觀選項的顯示名稱（i18n 字典的 key） */
export const THEME_LABEL = { system: "theme.system", light: "theme.light", dark: "theme.dark" } as const satisfies Record<
  ThemeChoice,
  string
>;

// 整個頁面共用一份外觀狀態：導覽列與結果頁「⋯」選單裡的外觀選項同時顯示同一個值
let current: ThemeChoice = read();
const listeners = new Set<() => void>();

function setCurrent(c: ThemeChoice) {
  if (c === current) return;
  current = c;
  apply(c);
  for (const fn of listeners) fn();
}

function subscribe(fn: () => void) {
  if (listeners.size === 0) window.addEventListener("storage", onStorage);
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) window.removeEventListener("storage", onStorage);
  };
}

// 其他分頁改了外觀時同步
function onStorage(e: StorageEvent) {
  if (e.key === KEY || e.key === null) setCurrent(read());
}

function setTheme(c: ThemeChoice) {
  try {
    if (c === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, c);
  } catch {
    /* 無痕模式寫不進去就只套用這一次 */
  }
  setCurrent(c);
}

/** 外觀：跟隨系統（預設）、淺色、深色；手動選的記在 localStorage（index.html 在第一次繪製前套用）。 */
export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void] {
  const choice = useSyncExternalStore(subscribe, () => current);
  return [choice, setTheme];
}
