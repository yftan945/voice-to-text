import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Job } from "./types";

/** 排隊中或處理中（任務頁列出、導覽列「任務」徽章計數的對象）。 */
export const isActive = (j: Job) => j.status === "queued" || j.status === "running";

const byNewest = (a: Job, b: Job) => b.created_at - a.created_at;

/** 分頁切到背景多久後關掉 SSE（回前景再重開） */
const HIDDEN_CLOSE_MS = 5000;
/** SSE 被瀏覽器放棄（CLOSED）後的重連間隔：1、2、5、10 秒，之後每 30 秒 */
const RETRY_MS = [1000, 2000, 5000, 10000, 30000];

interface JobsValue {
  /** 所有任務（新的在前）；還沒載入時為 null */
  jobs: Job[] | null;
  error: string;
  /** 佇列操作、刪除、重新轉錄回傳的最新任務併回列表 */
  merge: (list: Job[]) => void;
  remove: (id: string) => void;
}

const JobsContext = createContext<JobsValue>({ jobs: null, error: "", merge: () => {}, remove: () => {} });

/**
 * 所有任務：先讀一次列表，之後靠 SSE（/api/events）只收有變動的任務。
 * 整個 App 共用一條連線：首頁「已完成」、任務頁、結果頁、導覽列的進行中徽章都讀這裡，不各自開 SSE。
 */
export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    // SSE 的第一個事件就是全部任務；比 api.jobs() 早回來時直接當初始清單
    api.jobs().then(
      (list) => setJobs((prev) => prev ?? list),
      (e) => setError(e.message),
    );
    let es: EventSource | null = null;
    let hideTimer = 0;
    let retryTimer = 0;
    let retries = 0;
    const open = () => {
      if (es) return;
      window.clearTimeout(retryTimer);
      const src = new EventSource("/api/events");
      es = src;
      src.addEventListener("jobs", (ev) => {
        const { changed, removed, full } = JSON.parse((ev as MessageEvent).data) as {
          changed: Job[];
          removed: string[];
          full?: boolean;
        };
        retries = 0;
        setError("");
        setJobs((prev) => {
          // 連線（含斷線重連）後的第一個事件是完整清單：整份替換，斷線期間被刪掉的任務才不會留著
          if (!prev || full) return [...changed].sort(byNewest);
          const map = new Map(prev.map((j) => [j.id, j]));
          for (const j of changed) map.set(j.id, j);
          for (const id of removed) map.delete(id);
          return [...map.values()].sort(byNewest);
        });
      });
      src.onerror = () => {
        // CONNECTING：瀏覽器自己會重連；CLOSED（例如伺服器回錯誤狀態）：瀏覽器放棄了，自己退避後重開
        if (src.readyState !== EventSource.CLOSED || es !== src) return;
        src.close();
        es = null;
        setError("與伺服器的連線中斷，正在重新連線…");
        const ms = RETRY_MS[Math.min(retries, RETRY_MS.length - 1)];
        retries += 1;
        retryTimer = window.setTimeout(() => {
          if (!document.hidden) open();
        }, ms);
      };
    };
    const close = () => {
      window.clearTimeout(retryTimer);
      window.clearTimeout(hideTimer);
      es?.close();
      es = null;
    };
    // 正式環境是 HTTP/1.1：同一主機最多 6 條連線，所有分頁共用。每個分頁只開這一條，而且：
    // - 離開頁面（重新整理、輸入網址換頁）時主動關掉，不然瀏覽器可能留著舊連線，下一頁會卡住載不出來；
    //   從上一頁快取（bfcache）回來時重新連線，第一個事件是完整清單。
    // - 分頁切到背景一段時間就關掉，回到前景再重開（第一個事件是完整清單，背景期間的變動不會漏掉）。
    const onShow = (e: PageTransitionEvent) => {
      if (e.persisted && !document.hidden) open();
    };
    const onVisibility = () => {
      window.clearTimeout(hideTimer);
      if (document.hidden) hideTimer = window.setTimeout(close, HIDDEN_CLOSE_MS);
      else open();
    };
    if (!document.hidden) open();
    else hideTimer = window.setTimeout(close, HIDDEN_CLOSE_MS);
    window.addEventListener("pagehide", close);
    window.addEventListener("pageshow", onShow);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", close);
      window.removeEventListener("pageshow", onShow);
      document.removeEventListener("visibilitychange", onVisibility);
      close();
    };
  }, []);
  // SSE 之後也會推送同樣的變動；先併回來畫面才不用等下一秒
  const merge = useCallback(
    (list: Job[]) =>
      setJobs((prev) => {
        if (!prev) return prev;
        const map = new Map(prev.map((j) => [j.id, j]));
        for (const j of list) map.set(j.id, j);
        return [...map.values()].sort(byNewest);
      }),
    [],
  );
  const remove = useCallback((id: string) => setJobs((prev) => prev?.filter((j) => j.id !== id) ?? prev), []);
  const value = useMemo(() => ({ jobs, error, merge, remove }), [jobs, error, merge, remove]);
  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export const useJobs = () => useContext(JobsContext);

/** 排隊中＋處理中的任務數（導覽列徽章）；還沒載入時為 0。 */
export function useActiveCount(): number {
  const { jobs } = useJobs();
  return useMemo(() => jobs?.filter(isActive).length ?? 0, [jobs]);
}
