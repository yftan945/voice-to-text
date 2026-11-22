import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { captionText } from "../../components/YoutubeInput";
import { duration, fileSize } from "../../format";
import { showMsg, useT, type TFn } from "../../i18n";
import type { JobOptionsValue } from "../../types";
import { itemName, itemSize, type SpeakerChoice, type UploadItem } from "./useUploader";

const MAX_SPEAKERS = 20;

function stateText(t: TFn, item: UploadItem, pct: number): string {
  const v = !!item.video;
  switch (item.state) {
    case "pending":
      return v ? t("up.waitAdd") : t("up.waitUpload");
    case "uploading":
      return v ? t("up.adding") : pct < 100 ? t("up.uploadingPct", { pct }) : t("up.checking");
    case "done":
      return t("up.queued");
    default:
      return v ? t("up.addFailed") : t("up.uploadFailed");
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
  const t = useT();
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
    fileCount ? t("up.files", { n: fileCount, size: fileSize(totalBytes) }) : "",
    videoCount ? t("up.videos", { n: videoCount }) : "",
  ]
    .filter(Boolean)
    .join(t("common.listSep"));
  const started = uploading || doneCount > 0 || failedCount > 0;
  const batchSpeakers = opts.numSpeakers ? t("opts.nPeople", { n: opts.numSpeakers }) : t("home.auto");
  const kind = videoCount && !fileCount ? "video" : fileCount && !videoCount ? "file" : "item";

  return (
    <section className="sec upload-sec" aria-labelledby="up-title">
      <div className="sec-head">
        <h2 id="up-title">{uploading ? t("up.titleUploading") : t("up.title")}</h2>
        <span className="sub num" aria-live="polite">
          {started
            ? uploading && currentIndex >= 0
              ? t("up.progressNow", { i: currentIndex + 1, total })
              : t("up.progressDone", { done: doneCount, total }) +
                (failedCount ? t("up.progressFailed", { n: failedCount }) : "")
            : summary}
        </span>
        <span className="grow" />
        {uploading && (
          <button type="button" className="tbtn danger" onClick={onStop}>
            {t("up.stop")}
          </button>
        )}
      </div>
      {started && (
        <div className={`bar batch-bar${uploading ? " now" : ""}`} role="progressbar" aria-label={t("up.batchProgress")}
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
                  {v && <span>{[v.channel, v.duration ? duration(v.duration) : ""].filter(Boolean).join(t("common.sep"))}</span>}
                  {v && <span>{captionText(v.captionLang)}</span>}
                  <span className={`up-state ${item.state}`}>{stateText(t, item, pct)}</span>
                </div>
                {item.file && (item.state === "uploading" || item.state === "done") && (
                  <div className="up-prog">
                    <div className={`bar${item.state === "uploading" ? " now" : ""}`} role="progressbar"
                      aria-label={t("up.itemProgress", { name })} aria-valuenow={item.state === "done" ? 100 : pct}
                      aria-valuemin={0} aria-valuemax={100}>
                      <i style={{ width: `${item.state === "done" ? 100 : pct}%` }} />
                    </div>
                    <span className="num">
                      {fileSize(item.state === "done" ? item.file.size : item.loaded)} / {fileSize(item.file.size)}
                    </span>
                  </div>
                )}
                {item.state === "failed" && item.error && <p className="err">{showMsg(t, item.error)}</p>}
              </div>
              <div className="up-acts">
                {opts.diarize && editable && (
                  <label className="up-spk">
                    <span className="sr-only">{t("up.itemSpeakers", { name })}</span>
                    <select
                      className="field"
                      value={String(item.speakers)}
                      onChange={(e) => {
                        const val = e.target.value;
                        onSpeakers(item.key, val === "inherit" || val === "auto" ? val : Number(val));
                      }}
                    >
                      <option value="inherit">{t("up.inherit", { value: batchSpeakers })}</option>
                      <option value="auto">{t("opts.autoCount")}</option>
                      {Array.from({ length: MAX_SPEAKERS }, (_, k) => k + 1).map((n) => (
                        <option key={n} value={n}>
                          {t("opts.nPeople", { n })}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {item.state === "failed" && item.retryable && (
                  <button type="button" className="tbtn ink" onClick={() => onRetry(item.key)}>
                    {t("up.retry")}
                  </button>
                )}
                {editable && (
                  <button type="button" className="tbtn danger" onClick={() => onRemove(item.key)} aria-label={t("up.removeLabel", { name })}>
                    {t("up.remove")}
                  </button>
                )}
                {item.state === "done" && item.jobId && (
                  <Link className="tbtn ink" to={`/jobs/${item.jobId}`}>
                    {t("up.viewProgress")}
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
            {pending === 0 ? t("up.nothing") : total === 1 ? t("up.start") : t("up.startN", { n: pending, kind })}
          </button>
        )}
        <p className="help">
          {uploading ? <strong>{t("up.keepOpen")}</strong> : t("up.keepOpen")}
          {t("up.howItWorks")}
        </p>
      </div>
    </section>
  );
}
