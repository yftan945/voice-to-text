import { useState } from "react";
import { ApiError, api } from "../api";
import { captionSourceLang, languageName } from "../format";
import type { Job, JobOptionsValue } from "../types";
import JobOptionsForm from "./JobOptionsForm";
import Modal from "./Modal";
import { useToast } from "./Toast";

/** YouTube 有人工字幕時的「文字來源」選擇：用字幕或用模型轉錄（重新轉錄、修改排隊中設定共用）。 */
function CaptionChoice({
  lang,
  value,
  onChange,
  modelHint,
}: {
  lang: string;
  value: boolean;
  onChange: (v: boolean) => void;
  modelHint: string;
}) {
  return (
    <fieldset>
      <legend className="lab">文字來源</legend>
      <div className="choice-list">
        <label className="choice">
          <input type="radio" name="text-source" checked={value} onChange={() => onChange(true)} />
          <span>
            <strong>用 YouTube 人工字幕（{languageName(lang)}）</strong>
            <small>直接用上傳者提供的字幕文字與時間，不跑語音辨識（較快）；講者辨識、字典照常</small>
          </span>
        </label>
        <label className="choice">
          <input type="radio" name="text-source" checked={!value} onChange={() => onChange(false)} />
          <span>
            <strong>用模型轉錄</strong>
            <small>{modelHint}</small>
          </span>
        </label>
      </div>
    </fieldset>
  );
}

export function DeleteDialog({
  job,
  onClose,
  onDone,
}: {
  job: Job;
  onClose: () => void;
  onDone: (mode: "transcript" | "audio" | "all", job: Job | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (mode: "transcript" | "audio" | "all") => {
    setBusy(true);
    setError("");
    try {
      const r = await api.deleteJob(job.id, mode);
      onDone(mode, r.job);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <Modal
      title="刪除"
      onClose={onClose}
      initialFocus="close"
      footer={
        <button type="button" className="btn" onClick={onClose}>
          取消
        </button>
      }
    >
      <p className="break">{job.filename}</p>
      <div className="choice-list">
        <button type="button" className="choice" disabled={busy || !job.has_transcript} onClick={() => run("transcript")}>
          <span>
            <strong>只刪逐字稿</strong>
            <small>逐字稿（含你的修改）刪除後無法復原；保留音檔，之後可以換模型或字典重新轉錄</small>
          </span>
        </button>
        <button type="button" className="choice" disabled={busy || !job.has_audio} onClick={() => run("audio")}>
          <span>
            <strong>只刪音檔</strong>
            <small>
              {job.source === "youtube"
                ? "保留逐字稿；結果頁只能用嵌入的 YouTube 影片播放，也不能重新轉錄"
                : "保留逐字稿；結果頁將無法播放，也不能重新轉錄"}
            </small>
          </span>
        </button>
        <button type="button" className="choice danger" disabled={busy} onClick={() => run("all")}>
          <span>
            <strong>整筆刪除</strong>
            <small>音檔與逐字稿全部刪除，無法復原</small>
          </span>
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}

export function RerunDialog({ job, onClose, onDone }: { job: Job; onClose: () => void; onDone: (j: Job) => void }) {
  const [opts, setOpts] = useState<JobOptionsValue>({
    model: job.model,
    diarize: job.diarize,
    numSpeakers: job.num_speakers,
    dictionaryIds: job.dictionary_ids,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // YouTube 有人工字幕時可以選「用字幕」或「用模型轉錄」
  const captionLang = job.source === "youtube" ? job.caption_lang : null;
  const [useCaptions, setUseCaptions] = useState(
    captionLang ? (job.caption_source ? captionSourceLang(job.caption_source) !== null : job.use_captions) : false,
  );
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      onDone(await api.rerun(job.id, opts, job.source === "youtube" ? useCaptions : undefined));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <Modal
      title="重新轉錄"
      wide
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn primary" disabled={busy} onClick={submit}>
            {busy ? "送出中…" : "開始重新轉錄"}
          </button>
        </>
      }
    >
      <p>重新轉錄完成後會取代目前的逐字稿（含編輯內容）。</p>
      {captionLang && (
        <CaptionChoice lang={captionLang} value={useCaptions} onChange={setUseCaptions} modelHint="用下面選的辨識模型重新聽一次" />
      )}
      <JobOptionsForm value={opts} onChange={setOpts} />
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}

/**
 * 修改排隊中任務的設定（任務頁）：模型、講者辨識與人數、字典；YouTube 有人工字幕時可選文字來源。
 * 409（已經開始處理或已結束）提示後關閉；400 等錯誤顯示在對話框裡。手機上 Modal 本身就是底部面板的樣子。
 */
export function OptionsDialog({
  job,
  onClose,
  onDone,
  onConflict,
}: {
  job: Job;
  onClose: () => void;
  onDone: (j: Job) => void;
  /** 409（已經開始處理或已結束）：關閉對話框並由呼叫端決定焦點；沒給就當成一般關閉 */
  onConflict?: () => void;
}) {
  const toast = useToast();
  const [opts, setOpts] = useState<JobOptionsValue>({
    model: job.model,
    diarize: job.diarize,
    numSpeakers: job.num_speakers,
    dictionaryIds: job.dictionary_ids,
  });
  const captionLang = job.source === "youtube" ? job.caption_lang : null;
  const [useCaptions, setUseCaptions] = useState(captionLang ? job.use_captions : false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      onDone(await api.updateOptions(job.id, opts, captionLang ? useCaptions : undefined));
      toast("已更新設定");
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409) {
        // 任務已經被 worker 領走或結束：設定沒有改，關掉對話框
        toast(err.message);
        (onConflict ?? onClose)();
        return;
      }
      setError(err.message);
      setBusy(false);
    }
  };
  return (
    <Modal
      title="修改設定"
      wide
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn primary" disabled={busy || !opts.model} onClick={submit}>
            {busy ? "儲存中…" : "儲存"}
          </button>
        </>
      }
    >
      <p className="break">{job.filename}</p>
      <p>還在排隊中，開始處理前都可以修改；排隊順序不變。</p>
      {captionLang && (
        <CaptionChoice lang={captionLang} value={useCaptions} onChange={setUseCaptions} modelHint="用下面選的辨識模型轉錄" />
      )}
      <JobOptionsForm value={opts} onChange={setOpts} />
      {error && <p className="error" role="alert">{error}</p>}
    </Modal>
  );
}
