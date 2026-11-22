import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { captionText } from "../../components/YoutubeInput";
import { duration, fileSize } from "../../format";
import type { JobOptionsValue } from "../../types";
import { itemName, itemSize, type SpeakerChoice, type UploadItem } from "./useUploader";

const MAX_SPEAKERS = 20;

function stateText(item: UploadItem, pct: number): string {
  const v = !!item.video;
  switch (item.state) {
    case "pending":
      return v ? "等待加入" : "等待上傳";
    case "uploading":
      return v ? "加入佇列中…" : pct < 100 ? `上傳中 ${pct}%` : "檢查檔案中…";
    case "done":
      return "已加入佇列";
    default:
      return v ? "加入失敗" : "上傳失敗";
  }
}

/** 首頁「要轉錄的錄音」：選好的檔案與影片、每筆的講者人數、上傳進度、失敗重試、開始與停止。 */
export default function UploadSection({
  items,
  opts,
  uploading,
  onStart,
  onStop,
  onRetry,
  onRemove,
  onSpeakers,
  settings,
}: {
  /** 手機：開始轉錄按鈕上方的設定摘要（設定收在底部面板裡，看不到） */
  settings?: ReactNode;
  items: UploadItem[];
  opts: JobOptionsValue;
  uploading: boolean;
  onStart: () => void;
  onStop: () => void;
  onRetry: (key: number) => void;
  onRemove: (key: number) => void;
  onSpeakers: (key: number, v: SpeakerChoice) => void;
}) {
  if (items.length === 0) return null;
  const total = items.length;
  const pending = items.filter((i) => i.state === "pending").length;
  const doneCount = items.filter((i) => i.state === "done").length;
  const failedCount = items.filter((i) => i.state === "failed").length;
  const currentIndex = items.findIndex((i) => i.state === "uploading");
  const fileCount = items.filter((i) => i.file).length;
  const videoCount = total - fileCount;
  const totalBytes = items.reduce((s, i) => s + itemSize(i), 0);
  const sentBytes = items.reduce(
    (s, i) => s + (i.state === "done" ? itemSize(i) : i.state === "uploading" ? i.loaded : 0),
    0,
  );
  // 只有 YouTube 影片（不用上傳）時，整批進度用完成的個數
  const batchProgress = totalBytes ? sentBytes / totalBytes : total ? doneCount / total : 0;
  const summary = [
    fileCount ? `${fileCount} 個檔案，${fileSize(totalBytes)}` : "",
    videoCount ? `${videoCount} 支 YouTube 影片` : "",
  ]
    .filter(Boolean)
    .join("、");
  const started = uploading || doneCount > 0 || failedCount > 0;
  const batchSpeakers = opts.numSpeakers ? `${opts.numSpeakers} 人` : "自動";
  const noun = videoCount && !fileCount ? "支影片" : fileCount && !videoCount ? "個檔案" : "個項目";

  return (
    <section className="sec upload-sec" aria-labelledby="up-title">
      <div className="sec-head">
        <h2 id="up-title">{uploading ? "上傳中" : "要轉錄的錄音"}</h2>
        <span className="sub num" aria-live="polite">
          {started
            ? uploading && currentIndex >= 0
              ? `整批第 ${currentIndex + 1} 個，共 ${total} 個`
              : `已加入 ${doneCount} 個，共 ${total} 個${failedCount ? `，${failedCount} 個失敗` : ""}`
            : summary}
        </span>
        <span className="grow" />
        {uploading && (
          <button type="button" className="tbtn danger" onClick={onStop}>
            停止上傳
          </button>
        )}
      </div>
      {started && (
        <div className={`bar batch-bar${uploading ? " now" : ""}`} role="progressbar" aria-label="整批進度"
          aria-valuenow={Math.round(batchProgress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${Math.round(batchProgress * 100)}%` }} />
        </div>
      )}
      <ul className="up-list">
        {items.map((item) => {
          const pct = item.total ? Math.round((item.loaded / item.total) * 100) : 0;
          const editable = item.state === "pending" || item.state === "failed";
          const v = item.video;
          const name = itemName(item);
          return (
            <li key={item.key} className={`up ${item.state}`}>
              <div className="up-main">
                <div className="nm break">
                  {v && <span className="src">YouTube</span>}
                  {name}
                </div>
                <div className="up-meta num">
                  {item.file && <span>{fileSize(item.file.size)}</span>}
                  {v && <span>{[v.channel, v.duration ? duration(v.duration) : ""].filter(Boolean).join("，")}</span>}
                  {v && <span>{captionText(v.captionLang)}</span>}
                  <span className={`up-state ${item.state}`}>{stateText(item, pct)}</span>
                </div>
                {item.file && (item.state === "uploading" || item.state === "done") && (
                  <div className="up-prog">
                    <div className={`bar${item.state === "uploading" ? " now" : ""}`} role="progressbar"
                      aria-label={`${name} 上傳進度`} aria-valuenow={item.state === "done" ? 100 : pct}
                      aria-valuemin={0} aria-valuemax={100}>
                      <i style={{ width: `${item.state === "done" ? 100 : pct}%` }} />
                    </div>
                    <span className="num">
                      {fileSize(item.state === "done" ? item.file.size : item.loaded)} / {fileSize(item.file.size)}
                    </span>
                  </div>
                )}
                {item.state === "failed" && <p className="err">{item.error}</p>}
              </div>
              <div className="up-acts">
                {opts.diarize && editable && (
                  <label className="up-spk">
                    <span className="sr-only">{name} 的講者人數</span>
                    <select
                      className="field"
                      value={String(item.speakers)}
                      onChange={(e) => {
                        const val = e.target.value;
                        onSpeakers(item.key, val === "inherit" || val === "auto" ? val : Number(val));
                      }}
                    >
                      <option value="inherit">講者人數跟著整批（{batchSpeakers}）</option>
                      <option value="auto">自動判斷人數</option>
                      {Array.from({ length: MAX_SPEAKERS }, (_, k) => k + 1).map((n) => (
                        <option key={n} value={n}>
                          {n} 人
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {item.state === "failed" && item.retryable && (
                  <button type="button" className="tbtn ink" onClick={() => onRetry(item.key)}>
                    重試
                  </button>
                )}
                {editable && (
                  <button type="button" className="tbtn danger" onClick={() => onRemove(item.key)} aria-label={`移除 ${name}`}>
                    移除
                  </button>
                )}
                {item.state === "done" && item.jobId && (
                  <Link className="tbtn ink" to={`/jobs/${item.jobId}`}>
                    查看進度
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {settings && pending > 0 && !uploading && settings}
      <div className="up-foot">
        {!uploading && (
          <button type="button" className="btn primary big" disabled={pending === 0 || !opts.model} onClick={onStart}>
            {pending === 0 ? "沒有等待處理的項目" : total === 1 ? "開始轉錄" : `開始轉錄（${pending} ${noun}）`}
          </button>
        )}
        <p className="help">
          {uploading ? <strong>上傳完成前請不要關閉這個頁面。</strong> : "上傳完成前請不要關閉這個頁面。"}
          檔案會一個一個上傳，傳完就在背景排隊處理；YouTube 影片由伺服器下載。
        </p>
      </div>
    </section>
  );
}
