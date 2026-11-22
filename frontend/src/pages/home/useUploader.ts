import { useEffect, useRef, useState } from "react";
import { ApiError, api, loadModels, newBatchId, uploadJob } from "../../api";
import type { YoutubePick } from "../../components/YoutubeInput";
import { msg, textMsg, type Msg } from "../../i18n";
import type { JobOptionsValue } from "../../types";

export type ItemState = "pending" | "uploading" | "done" | "failed";
/** 每個檔案的講者人數：inherit＝跟著整批設定、auto＝自動判斷、數字＝指定 N 人 */
export type SpeakerChoice = "inherit" | "auto" | number;

/** 清單裡的一項：上傳的檔案（file）或 YouTube 影片（video，由伺服器下載）。 */
export interface UploadItem {
  key: number;
  file: File | null;
  video: YoutubePick | null;
  speakers: SpeakerChoice;
  state: ItemState;
  loaded: number;
  total: number;
  /** 失敗原因（前端自己的字樣存 i18n key，換語言時跟著換）；沒有為 null */
  error: Msg | null;
  /** 失敗原因重試也不會好（例如檔案太大、沒有音軌）時不顯示重試 */
  retryable: boolean;
  jobId: string | null;
}

const MB = 1024 * 1024;

const sameFile = (a: File, b: File) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
export const itemName = (i: UploadItem) => i.file?.name ?? i.video?.title ?? "";
export const itemSize = (i: UploadItem) => i.file?.size ?? 0;

function resolveSpeakers(choice: SpeakerChoice, o: JobOptionsValue): number | null {
  if (choice === "inherit") return o.numSpeakers;
  if (choice === "auto") return null;
  return choice;
}

const blank = (key: number): Omit<UploadItem, "file" | "video" | "total"> => ({
  key,
  speakers: "inherit",
  state: "pending",
  loaded: 0,
  error: null,
  retryable: true,
  jobId: null,
});

/**
 * 批次上傳：檔案一次傳一個（避免手機頻寬被塞滿）、YouTube 只建立任務；整批共用同一份設定與 batch_id。
 * 單一項目失敗不影響其他項目，可重試（重試也沒用的錯誤不給重試）。onFinished 在整批結束時呼叫（停止時不呼叫），
 * added 是這一輪（這次開始或重試）新加入佇列的項目，不含之前已經加入的。
 */
export function useUploader(onFinished: (items: UploadItem[], added: UploadItem[]) => void) {
  const [items, setItems] = useState<UploadItem[]>([]);
  // 上傳迴圈是 async，讀最新的清單要用 ref（state 要等 render 後才更新）
  const itemsRef = useRef<UploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  /** 清單上方的提示（i18n 字典的 key，換語言時跟著換）；沒有為 "" */
  const [notice, setNotice] = useState<"" | "up.dupVideos" | "up.dupFiles">("");
  const keySeq = useRef(0);
  const runningRef = useRef(false);
  const stopRef = useRef(false);
  const abortRef = useRef<(() => void) | null>(null);
  const batchIdRef = useRef<string | null>(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  const commit = (next: UploadItem[]) => {
    itemsRef.current = next;
    setItems(next);
  };
  const patch = (key: number, p: Partial<UploadItem>) =>
    commit(itemsRef.current.map((i) => (i.key === key ? { ...i, ...p } : i)));

  // 離開頁面時停止還沒傳完的上傳
  useEffect(
    () => () => {
      stopRef.current = true;
      abortRef.current?.();
    },
    [],
  );

  /** 加入影片（略過清單裡已經有的）；回傳實際加入的支數。 */
  const addVideos = (videos: YoutubePick[]): number => {
    const existing = itemsRef.current;
    const seen = new Set(existing.filter((i) => i.state !== "done" && i.video).map((i) => i.video!.videoId));
    const fresh: YoutubePick[] = [];
    for (const v of videos) {
      if (seen.has(v.videoId)) continue;
      seen.add(v.videoId);
      fresh.push(v);
    }
    setNotice(fresh.length < videos.length ? "up.dupVideos" : "");
    commit([...existing, ...fresh.map((video) => ({ ...blank(++keySeq.current), file: null, video, total: 0 }))]);
    return fresh.length;
  };

  const addFiles = (files: File[]) => {
    const existing = itemsRef.current;
    const fresh = files.filter((f) => !existing.some((i) => i.state !== "done" && i.file && sameFile(i.file, f)));
    setNotice(fresh.length < files.length ? "up.dupFiles" : "");
    commit([...existing, ...fresh.map((file) => ({ ...blank(++keySeq.current), file, video: null, total: file.size }))]);
  };

  const run = async (opts: JobOptionsValue) => {
    if (runningRef.current) return;
    runningRef.current = true;
    stopRef.current = false;
    setNotice("");
    setUploading(true);
    // 設定在上傳期間鎖住，整批共用同一份
    const o = opts;
    // 清單裡已經有上傳完成的檔案（重試或續傳）時沿用同一個批次
    if (!batchIdRef.current || !itemsRef.current.some((i) => i.state === "done")) batchIdRef.current = newBatchId();
    const batchId = batchIdRef.current;
    const doneBefore = new Set(itemsRef.current.filter((i) => i.state === "done").map((i) => i.key));
    const limitMb = await loadModels().then(
      (m) => m.max_upload_mb,
      () => undefined,
    );
    for (;;) {
      if (stopRef.current) break;
      const next = itemsRef.current.find((i) => i.state === "pending");
      if (!next) break;
      const itemOpts = { ...o, numSpeakers: resolveSpeakers(next.speakers, o) };
      if (next.video) {
        // YouTube：只建立任務（伺服器下載），不用上傳
        patch(next.key, { state: "uploading", error: null });
        try {
          const job = await api.createYoutubeJob(next.video.videoId, itemOpts, batchId);
          patch(next.key, { state: "done", jobId: job.id });
        } catch (e) {
          const err = e as ApiError;
          // 400／422（影片無法處理、參數錯誤）重試也一樣；網路或 YouTube 暫時的問題可以重試
          patch(next.key, { state: "failed", error: textMsg(err.message), retryable: err.status !== 400 && err.status !== 422 });
        }
        continue;
      }
      if (!next.file) {
        patch(next.key, { state: "failed", error: msg("up.noFile"), retryable: false });
        continue;
      }
      const file = next.file;
      // 先在瀏覽器擋掉超過上限的檔案，不用等整個檔案傳完才被拒絕
      if (limitMb && file.size > limitMb * MB) {
        patch(next.key, { state: "failed", error: msg("up.tooLarge", { mb: limitMb }), retryable: false });
        continue;
      }
      patch(next.key, { state: "uploading", loaded: 0, total: file.size, error: null });
      const { promise, abort } = uploadJob(file, itemOpts, (_, loaded, total) => patch(next.key, { loaded, total }), batchId);
      abortRef.current = abort;
      try {
        const job = await promise;
        patch(next.key, { state: "done", jobId: job.id, loaded: file.size, total: file.size });
      } catch (e) {
        if (stopRef.current) {
          patch(next.key, { state: "pending", loaded: 0, error: null });
        } else {
          const err = e as ApiError;
          // 400（壞檔、沒有音軌）與 413（太大）重試也一樣；網路中斷或伺服器錯誤可以重試
          patch(next.key, { state: "failed", error: textMsg(err.message), retryable: err.status !== 400 && err.status !== 413 });
        }
      } finally {
        abortRef.current = null;
      }
    }
    runningRef.current = false;
    setUploading(false);
    if (!stopRef.current) {
      const all = itemsRef.current;
      finishedRef.current(all, all.filter((i) => i.state === "done" && !doneBefore.has(i.key)));
    }
  };

  const stop = () => {
    stopRef.current = true;
    abortRef.current?.();
  };

  const retry = (key: number, opts: JobOptionsValue) => {
    patch(key, { state: "pending", error: null, loaded: 0 });
    if (!runningRef.current) void run(opts);
  };

  const remove = (key: number) => commit(itemsRef.current.filter((i) => i.key !== key));
  /** 把已加入佇列的項目從清單拿掉（任務頁已經看得到它們）。 */
  const clearDone = () => commit(itemsRef.current.filter((i) => i.state !== "done"));
  const setSpeakers = (key: number, speakers: SpeakerChoice) => patch(key, { speakers });

  return { items, uploading, notice, addFiles, addVideos, run, stop, retry, remove, clearDone, setSpeakers };
}
