import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, exportUrl } from "../api";
import Icon from "../components/Icon";
import { DeleteDialog, RerunDialog } from "../components/JobDialogs";
import { MenuButton, type MenuItem } from "../components/Menu";
import { ConfirmModal } from "../components/Modal";
import StepBar from "../components/StepBar";
import Tick from "../components/Tick";
import { captionSourceLang, dateTime, duration, etaText, languageName } from "../format";
import { modelLabel, modelShort, useIsMobile, useModels, useStoredFlag, useUnsavedBlocker } from "../hooks";
import { useLang, useT, type StrKey } from "../i18n";
import { useJobs } from "../jobs";
import { PlayerContext, PlayerStore } from "../lib/player";
import { THEME_LABEL, useTheme, type ThemeChoice } from "../theme";
import type { Job, Transcript, TranscriptPayload } from "../types";
import ResultView, { mediaChoice, type MediaMode } from "./result/ResultView";

/** 匯出格式：[格式, 名稱（i18n 字典的 key）] */
const FORMATS = (timestamps: boolean): [string, StrKey][] => [
  ["txt", timestamps ? "export.txtTime" : "export.txtPlain"],
  ["md", "export.md"],
  ["srt", "export.srt"],
  ["vtt", "export.vtt"],
  ["json", "export.json"],
];

/**
 * 換語言時只換掉後端依語言 render 的欄位（警告、講者辨識失敗原因、文字來源說明），
 * 逐字稿內容（段落、講者）沿用原本的物件，結果區不用重來，播放、編輯中的狀態與開著的對話框都保留。
 */
function withTexts(t: Transcript, from: Transcript): Transcript {
  return {
    ...t,
    warnings: from.warnings,
    diarization: from.diarization ? { ...t.diarization, error: from.diarization.error } : t.diarization,
    text_source: t.text_source && from.text_source ? { ...t.text_source, label: from.text_source.label } : t.text_source,
  };
}

/** 首字大寫（英文的錯誤原因寫成句中片語，單獨當提示文字時要大寫；中文不受影響） */
const capFirst = (s: string) => s.charAt(0).toLocaleUpperCase() + s.slice(1);

/** 處理中／排隊中的進度：五格步驟、順位、預估時間、取消。 */
function Progress({ job, hasPayload, onCancel }: { job: Job; hasPayload: boolean; onCancel: () => void }) {
  const t = useT();
  return (
    <div className="r-progress" aria-live="polite">
      <div className="r-progress-head">
        <b className={job.status === "running" ? "run" : ""}>
          {job.status === "queued"
            ? job.queue_rank
              ? t("job.queuedRank", { rank: job.queue_rank }) +
                (job.queue_position ? t("job.ahead", { n: job.queue_position }) : "")
              : t("job.queued")
            : job.cancel_requested
              ? t("job.canceling")
              : `${job.stage} ${Math.round(job.progress * 100)}%`}
        </b>
        <span className="grow" />
        <button type="button" className="tbtn danger" onClick={onCancel} disabled={job.cancel_requested}>
          {job.cancel_requested ? t("job.canceling") : t("job.cancel")}
        </button>
      </div>
      {job.status === "running" && <StepBar job={job} />}
      <p className="help">
        {t("job.etaNote", {
          eta: etaText(job),
          basis: job.estimate_basis === "history" ? t("job.basisHistory") : t("job.basisDefault"),
        })}
        {job.source === "youtube" ? t("job.srcYt") : t("job.srcFile")}
        {hasPayload && t("job.willReplace")}
      </p>
    </div>
  );
}

/** 結果頁：標題與工具、處理進度或失敗原因、逐字稿（ResultView）。 */
export default function JobPage() {
  const t = useT();
  const lang = useLang();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const models = useModels();
  const [theme, setTheme] = useTheme();
  const [job, setJobState] = useState<Job | null>(null);
  const { jobs, merge, remove } = useJobs();
  // 自己這頁操作（取消、重新轉錄、刪除逐字稿或音檔）拿到的最新任務也併回共用清單，其他頁與徽章馬上更新
  const setJob = useCallback(
    (j: Job) => {
      setJobState(j);
      merge([j]);
    },
    [merge],
  );
  const [payload, setPayload] = useState<TranscriptPayload | null>(null);
  // 每次從伺服器重新載入逐字稿（例如重新轉錄完成、還原原始結果）就換一個版本號，讓結果區整個重來（清掉編輯中的狀態）
  const [version, setVersion] = useState(0);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [timeline, setTimeline] = useStoredFlag("v2t.timeline", true);
  const [follow, setFollow] = useStoredFlag("v2t.follow", true);
  const [mode, setMode] = useState<MediaMode>("video");
  // 影片無法播放的原因存 i18n key（換語言時跟著換）
  const [videoError, setVideoError] = useState<StrKey | null>(null);
  const [editDirty, setEditDirty] = useState(false);
  const store = useMemo(() => new PlayerStore(0), [id]);

  // 有還沒儲存的編輯：關閉分頁、站內換頁前先確認
  const blocker = useUnsavedBlocker(editDirty);

  // 讀取逐字稿的序號：每次重新讀取（整份或只換語言）都 +1，晚到的舊回應不套用
  const txSeq = useRef(0);
  const loadTranscript = useCallback(() => {
    const seq = ++txSeq.current;
    api.transcript(id).then(
      (p) => {
        if (seq !== txSeq.current) return;
        setPayload(p);
        setVersion((v) => v + 1);
      },
      () => {
        if (seq === txSeq.current) setPayload(null);
      },
    );
  }, [id]);

  useEffect(() => {
    setJobState(null);
    setPayload(null);
    setError("");
    setVideoError(null);
    txSeq.current += 1; // 上一筆任務還在路上的逐字稿不要套到這一筆
    api.job(id).then(
      (j) => {
        setJob(j);
        store.duration = j.duration ?? 0;
        if (j.has_transcript) loadTranscript();
      },
      (e) => setError(e.message),
    );
  }, [id, loadTranscript, store, setJob]);

  const active = job?.status === "queued" || job?.status === "running";

  // 編輯被伺服器以 409 擋下（別的分頁或裝置剛按了重新轉錄）：重新讀取任務，active 變成 true 後進入唯讀
  const refreshJob = useCallback(() => {
    api.job(id).then(setJob, () => {
      /* 讀不到就維持原狀，下次操作再試 */
    });
  }, [id, setJob]);

  // 進度與狀態：讀整個 App 共用的任務 SSE（jobs.tsx），不另外開這筆任務自己的連線（每個分頁最多 1 條）。
  // 欄位和 GET /api/jobs/{id} 相同。處理中變成完成時載入逐字稿；曾經在清單裡、後來不見了＝被刪除，回首頁。
  const live = jobs?.find((j) => j.id === id) ?? null;
  const loaded = job !== null;
  const jobRef = useRef(job);
  jobRef.current = job;
  const seenRef = useRef(false);
  useEffect(() => {
    seenRef.current = false;
  }, [id]);
  useEffect(() => {
    if (!live) {
      // 剛上傳完進來時，清單可能還沒收到這筆（下一個 SSE 事件才會有）：沒看過就不算刪除
      if (jobs && seenRef.current) navigate("/");
      return;
    }
    seenRef.current = true;
    const prev = jobRef.current;
    if (!prev || prev === live) return;
    setJobState(live);
    const wasActive = prev.status === "queued" || prev.status === "running";
    if (wasActive && live.status === "done") {
      setActionError("");
      loadTranscript();
    }
  }, [live, loaded, jobs, navigate, loadTranscript]);

  // 換語言時重新讀任務與逐字稿（後端依語言回傳的錯誤、警告換成新語言）。
  // 逐字稿只合併依語言 render 的欄位（見 withTexts），不換版本號：結果區不重來，播放與編輯中的狀態都保留。
  // 晚到的舊回應不套用；讀取失敗就維持原本的內容。
  const langRef = useRef(lang);
  const langSeq = useRef(0);
  useEffect(() => {
    if (langRef.current === lang) return;
    langRef.current = lang;
    const seq = ++langSeq.current;
    const before = jobRef.current;
    api.job(id).then(
      (j) => {
        // 這段期間 SSE 已經送來較新的任務（換語言時會重新連線、送整份清單）就不用這份
        if (seq === langSeq.current && jobRef.current === before) setJob(j);
      },
      () => {
        /* 讀不到就維持原狀 */
      },
    );
    if (!before?.has_transcript) return;
    const txAt = ++txSeq.current;
    api.transcript(id).then(
      (p) => {
        if (txAt !== txSeq.current) return;
        setPayload((prev) =>
          prev ? { ...prev, original: withTexts(prev.original, p.original), current: withTexts(prev.current, p.current) } : prev,
        );
      },
      () => {
        /* 讀不到就保留原本的逐字稿 */
      },
    );
  }, [lang, id, setJob]);

  useEffect(() => {
    document.title = job ? t("app.title", { page: job.filename }) : t("app.name");
    return () => {
      document.title = t("app.name");
    };
  }, [job?.filename, t]);

  if (error)
    return (
      <main className="page">
        <p className="err">{error}</p>
        <p>
          <Link to="/">{t("job.back")}</Link>
        </p>
      </main>
    );
  if (!job)
    return (
      <main className="page">
        <p className="empty">{t("common.loading")}</p>
      </main>
    );

  const cancel = async () => {
    setActionError("");
    try {
      setJob(await api.cancel(id));
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  const media = mediaChoice(job, mode, videoError);
  const captionLang = captionSourceLang(job.caption_source);
  const showToggle = !!payload && !!media.videoId && media.canPlay;

  const actionItems = (): MenuItem[] => [
    { label: t("job.rerunItem"), disabled: !job.has_audio || active, onSelect: () => setRerunning(true) },
    ...(payload ? [{ label: t("job.resetItem"), disabled: !payload.edited || active, onSelect: () => setConfirmReset(true) }] : []),
    ...(job.source === "youtube" && job.source_url ? [{ label: t("job.openYt"), href: job.source_url, external: true }] : []),
    { sep: true },
    { label: t("job.deleteItem"), danger: true, disabled: active, onSelect: () => setDeleting(true) },
  ];
  const exportItems = (): MenuItem[] =>
    FORMATS(timeline).map(([f, label]) => ({ label: t("export.item", { format: t(label) }), href: exportUrl(job.id, f, timeline) }));
  const mobileItems = (): MenuItem[] => [
    ...(payload
      ? [
          { header: t("export.title") },
          ...exportItems(),
          { sep: true } as MenuItem,
          { label: t("job.showTimestamps"), checked: timeline, onSelect: () => setTimeline(!timeline) },
          { label: t("job.follow"), checked: follow, onSelect: () => setFollow(!follow) },
          ...(showToggle
            ? [
                { header: t("job.mediaSource") },
                { label: t("job.srcVideo"), checked: media.useVideo, disabled: !media.videoOk, onSelect: () => setMode("video") },
                { label: t("job.srcAudio"), checked: media.useAudio, onSelect: () => setMode("audio") },
              ]
            : []),
          { sep: true } as MenuItem,
        ]
      : []),
    ...actionItems(),
    { sep: true },
    { header: t("theme.header") },
    ...(["system", "light", "dark"] as ThemeChoice[]).map((c) => ({
      label: t(THEME_LABEL[c]),
      checked: theme === c,
      onSelect: () => setTheme(c),
    })),
  ];

  const statusTop = (
    <>
      {actionError && (
        <p className="note error-note" role="alert">
          {actionError}
        </p>
      )}
      {active && <Progress job={job} hasPayload={!!payload} onCancel={cancel} />}
      {job.status === "failed" && (
        <div className="note error-note">
          <p>
            <b>{t("job.failed")}</b>
            {job.error}
          </p>
          {payload && <p>{t("job.lastGood")}</p>}
          {job.has_audio && (
            <div className="row">
              <button type="button" className="btn" onClick={() => setRerunning(true)}>
                {t("rerun.title")}
              </button>
            </div>
          )}
        </div>
      )}
      {job.status === "canceled" && (
        <p className="note">
          {t("job.canceled")}
          {job.has_audio && t("job.canceledRerun")}
        </p>
      )}
      {!active && job.status === "done" && !job.has_transcript && (
        <p className="note">
          {t("job.transcriptDeleted")}
          {job.has_audio ? t("job.canRerun") : t("job.cantRerun")}
        </p>
      )}
    </>
  );

  return (
    <PlayerContext.Provider value={store}>
      <main className={`result${payload ? "" : " no-transcript"}${media.useVideo && payload ? " with-video" : ""}`}>
        <header className="r-head">
          <Link className="back" to="/" aria-label={t("job.back")}>
            <Icon name="chevL" />
          </Link>
          <div className="titles">
            <h1 className="break">{job.filename}</h1>
            <p className="meta num">
              <span className="m-date">{dateTime(job.created_at)}</span>
              <span>{duration(job.duration)}</span>
              <span className="m-src">
                {captionLang
                  ? t("queue.captions", { lang: languageName(captionLang) })
                  : isMobile
                    ? t("home.modelShort", { name: modelShort(models, job.model) })
                    : modelLabel(models, job.model)}
              </span>
              <span className="m-spk">
                {job.diarize
                  ? job.num_speakers
                    ? t("job.speakersFixed", { n: job.num_speakers })
                    : t("job.speakersAuto")
                  : t("queue.noDiarize")}
              </span>
              {job.source === "youtube" && job.yt_channel && <span className="m-ch">{job.yt_channel}</span>}
              {job.source === "youtube" && job.source_url && (
                <a className="m-yt" href={job.source_url} target="_blank" rel="noopener noreferrer">
                  {t("job.openYt")}
                </a>
              )}
              {payload?.edited && <span className="m-ed">{t("job.edited")}</span>}
            </p>
          </div>
          <div className="head-tools">
            {payload && (
              <Tick on={timeline} onChange={setTimeline} className="d-only">
                {t("job.timestamps")}
              </Tick>
            )}
            {showToggle && (
              <div className="seg-toggle d-only" role="group" aria-label={t("job.mediaSource")}>
                <button
                  type="button"
                  aria-pressed={media.useVideo}
                  disabled={!media.videoOk}
                  title={videoError ? capFirst(t(videoError)) : t("job.videoTitle")}
                  onClick={() => setMode("video")}
                >
                  {t("job.video")}
                </button>
                <button type="button" aria-pressed={media.useAudio} title={t("job.audioTitle")} onClick={() => setMode("audio")}>
                  {t("job.audio")}
                </button>
              </div>
            )}
            {payload && (
              <MenuButton className="btn d-only" items={exportItems} aria-label={t("export.title")}>
                {t("export.title")}
              </MenuButton>
            )}
            <MenuButton className="btn icon" items={isMobile ? mobileItems : actionItems} aria-label={t("job.more")}>
              <Icon name="more" />
            </MenuButton>
          </div>
        </header>

        {payload ? (
          <ResultView
            key={version}
            job={job}
            payload={payload}
            onChange={setPayload}
            readOnly={active}
            timeline={timeline}
            follow={follow}
            onFollow={setFollow}
            isMobile={isMobile}
            store={store}
            mode={mode}
            videoError={videoError}
            onVideoError={setVideoError}
            onDirtyChange={setEditDirty}
            onBusy={refreshJob}
            top={statusTop}
            moreItems={mobileItems}
          />
        ) : (
          <div className="r-main">{statusTop}</div>
        )}

        {deleting && (
          <DeleteDialog
            job={job}
            onClose={() => setDeleting(false)}
            onDone={(m, j) => {
              setDeleting(false);
              if (!j) {
                remove(id);
                return navigate("/");
              }
              setJob(j);
              if (m === "transcript") {
                txSeq.current += 1; // 還在路上的讀取不要把刪掉的逐字稿放回來
                setPayload(null);
              }
            }}
          />
        )}
        {rerunning && (
          <RerunDialog
            job={job}
            onClose={() => setRerunning(false)}
            onDone={(j) => {
              setRerunning(false);
              setJob(j);
            }}
          />
        )}
        {confirmReset && (
          <ConfirmModal
            title={t("job.resetTitle")}
            message={t("job.resetMsg")}
            confirmLabel={t("job.resetConfirm")}
            danger
            onClose={() => setConfirmReset(false)}
            onConfirm={async () => {
              try {
                const p = await api.resetEdits(job.id);
                txSeq.current += 1;
                setPayload(p);
              } catch (e) {
                if (e instanceof ApiError && e.status === 409) {
                  setConfirmReset(false);
                  setActionError(e.message);
                  refreshJob();
                  return;
                }
                throw e;
              }
              setVersion((v) => v + 1);
              setEditDirty(false);
              setConfirmReset(false);
            }}
          />
        )}
        {blocker.state === "blocked" && (
          <ConfirmModal
            title={t("common.discardTitle")}
            message={t("job.leaveMsg")}
            confirmLabel={t("common.leave")}
            cancelLabel={t("common.keepEditing")}
            danger
            onClose={() => blocker.reset()}
            onConfirm={() => blocker.proceed()}
          />
        )}
      </main>
    </PlayerContext.Provider>
  );
}
