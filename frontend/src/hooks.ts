import { useCallback, useEffect, useState } from "react";
import { useBlocker } from "react-router-dom";
import { loadModels } from "./api";
import type { ModelList } from "./types";

/** 有未儲存的內容時，關閉分頁或重新整理前讓瀏覽器跳出確認。 */
export function useBeforeUnload(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [active]);
}

/** 有未儲存的內容時擋下站內換頁；回傳 blocker，blocked 時由頁面顯示確認對話框。 */
export function useUnsavedBlocker(active: boolean) {
  useBeforeUnload(active);
  return useBlocker(({ currentLocation, nextLocation }) => active && currentLocation.pathname !== nextLocation.pathname);
}

/** 使用者是否要求減少動態效果。 */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

/** CSS media query 是否成立（跟著視窗大小變動）。 */
export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => typeof window !== "undefined" && window.matchMedia?.(query).matches === true);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** 手機版面（與 CSS 的 max-width: 859px 一致）。 */
export const useIsMobile = () => useMediaQuery("(max-width: 859px)");

/** 桌機（滑鼠）才開拖曳與滑過才出現的按鈕；觸控裝置用按鈕或長按。 */
export const useFinePointer = () => useMediaQuery("(hover: hover) and (pointer: fine)");

/** 記在 localStorage 的開關（"1"／"0"）。 */
export function useStoredFlag(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [v, setV] = useState<boolean>(() => {
    try {
      const s = localStorage.getItem(key);
      return s === null ? initial : s === "1";
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (nv: boolean) => {
      try {
        localStorage.setItem(key, nv ? "1" : "0");
      } catch {
        /* 寫不進去就只在這次有效 */
      }
      setV(nv);
    },
    [key],
  );
  return [v, set];
}

/** 模型清單（整個頁面共用一次請求），用來把模型 id 顯示成名稱。 */
export function useModels(): ModelList | null {
  const [models, setModels] = useState<ModelList | null>(null);
  useEffect(() => {
    let alive = true;
    loadModels().then(
      (m) => alive && setModels(m),
      () => {
        /* 讀不到就顯示原始 id */
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  return models;
}

export function modelLabel(models: ModelList | null, id: string): string {
  return models?.models.find((m) => m.id === id)?.label ?? id;
}
