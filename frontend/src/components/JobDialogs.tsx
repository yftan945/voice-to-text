import { useState } from "react";
import { ApiError, api } from "../api";
import { captionSourceLang, languageName } from "../format";
import { useT } from "../i18n";
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
  const t = useT();
  return (
    <fieldset>
      <legend className="lab">{t("caption.legend")}</legend>
      <div className="choice-list">
        <label className="choice">
          <input type="radio" name="text-source" checked={value} onChange={() => onChange(true)} />
          <span>
            <strong>{t("caption.use", { lang: languageName(lang) })}</strong>
            <small>{t("caption.useHint")}</small>
          </span>
        </label>
        <label className="choice">
          <input type="radio" name="text-source" checked={!value} onChange={() => onChange(false)} />
          <span>
            <strong>{t("caption.model")}</strong>
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
  const t = useT();
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
      title={t("del.title")}
      onClose={onClose}
      initialFocus="close"
      footer={
        <button type="button" className="btn" onClick={onClose}>
          {t("common.cancel")}
        </button>
      }
    >
      <p className="break">{job.filename}</p>
      <div className="choice-list">
        <button type="button" className="choice" disabled={busy || !job.has_transcript} onClick={() => run("transcript")}>
          <span>
            <strong>{t("del.transcript")}</strong>
            <small>{t("del.transcriptHint")}</small>
          </span>
        </button>
        <button type="button" className="choice" disabled={busy || !job.has_audio} onClick={() => run("audio")}>
          <span>
            <strong>{t("del.audio")}</strong>
            <small>{job.source === "youtube" ? t("del.audioHintYt") : t("del.audioHint")}</small>
          </span>
        </button>
        <button type="button" className="choice danger" disabled={busy} onClick={() => run("all")}>
          <span>
            <strong>{t("del.all")}</strong>
            <small>{t("del.allHint")}</small>
          </span>
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}

export function RerunDialog({ job, onClose, onDone }: { job: Job; onClose: () => void; onDone: (j: Job) => void }) {
  const t = useT();
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
      title={t("rerun.title")}
      wide
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button type="button" className="btn primary" disabled={busy} onClick={submit}>
            {busy ? t("rerun.submitting") : t("rerun.submit")}
          </button>
        </>
      }
    >
      <p>{t("rerun.note")}</p>
      {captionLang && (
        <CaptionChoice lang={captionLang} value={useCaptions} onChange={setUseCaptions} modelHint={t("rerun.modelHint")} />
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
  const t = useT();
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
      toast(t("options.saved"));
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
      title={t("options.title")}
      wide
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button type="button" className="btn primary" disabled={busy || !opts.model} onClick={submit}>
            {busy ? t("common.saving") : t("common.save")}
          </button>
        </>
      }
    >
      <p className="break">{job.filename}</p>
      <p>{t("options.note")}</p>
      {captionLang && (
        <CaptionChoice lang={captionLang} value={useCaptions} onChange={setUseCaptions} modelHint={t("options.modelHint")} />
      )}
      <JobOptionsForm value={opts} onChange={setOpts} />
      {error && <p className="error" role="alert">{error}</p>}
    </Modal>
  );
}
