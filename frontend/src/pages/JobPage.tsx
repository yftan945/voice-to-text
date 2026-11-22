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
import { modelLabel, useIsMobile, useModels, useStoredFlag, useUnsavedBlocker } from "../hooks";
import { useJobs } from "../jobs";
import { PlayerContext, PlayerStore } from "../lib/player";
import { THEME_LABEL, useTheme, type ThemeChoice } from "../theme";
import type { Job, TranscriptPayload } from "../types";
import ResultView, { mediaChoice, type MediaMode } from "./result/ResultView";

const FORMATS = (timestamps: boolean): [string, string][] => [
  ["txt", timestamps ? "TXT（含時間）" : "TXT（純文字）"],
  ["md", "Markdown"],
  ["srt", "SRT 字幕"],
  ["vtt", "VTT 字幕"],
  ["json", "JSON"],
];

/** 處理中／排隊中的進度：五格步驟、順位、預估時間、取消。 */
function Progress({ job, hasPayload, onCancel }: { job: Job; hasPayload: boolean; onCancel: () => void }) {
  return (
    <div className="r-progress" aria-live="polite">
      <div className="r-progress-head">
        <b className={job.status === "running" ? "run" : ""}>
          {job.status === "queued"
            ? job.queue_rank
              ? `排隊中，第 ${job.queue_rank} 位${job.queue_position ? `（前面還有 ${job.queue_position} 個任務）` : ""}`
              : "排隊中"
            : job.cancel_requested
              ? "取消中…"
              : `${job.stage} ${Math.round(job.progress * 100)}%`}
        </b>
        <span className="grow" />
        <button type="button" className="tbtn danger" onClick={onCancel} disabled={job.cancel_requested}>
          {job.cancel_requested ? "取消中…" : "取消任務"}
        </button>
      </div>
      {job.status === "running" && <StepBar job={job} />}
      <p className="help">
        {etaText(job)}（{job.estimate_basis === "history" ? "依過去實際處理速度估算" : "依預設速度估算"}，僅供參考）。
        {job.source === "youtube" ? "由伺服器下載與處理" : "檔案已上傳完成"}，關掉頁面也會繼續跑。
        {hasPayload && "完成後會取代下面目前的逐字稿；處理期間不能編輯。"}
      </p>
    </div>
  );
}

/** 結果頁：標題與工具、處理進度或失敗原因、逐字稿（ResultView）。 */
export default function JobPage() {
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
  const [videoError, setVideoError] = useState<string | null>(null);
  const [editDirty, setEditDirty] = useState(false);
  const store = useMemo(() => new PlayerStore(0), [id]);

  // 有還沒儲存的編輯：關閉分頁、站內換頁前先確認
  const blocker = useUnsavedBlocker(editDirty);

  const loadTranscript = useCallback(() => {
    api.transcript(id).then(
      (p) => {
        setPayload(p);
        setVersion((v) => v + 1);
      },
      () => setPayload(null),
    );
  }, [id]);

  useEffect(() => {
    setJobState(null);
    setPayload(null);
    setError("");
    setVideoError(null);
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

  useEffect(() => {
    document.title = job ? `${job.filename}・語音轉文字` : "語音轉文字";
    return () => {
      document.title = "語音轉文字";
    };
  }, [job?.filename]);

  if (error)
    return (
      <main className="page">
        <p className="err">{error}</p>
        <p>
          <Link to="/">回到錄音</Link>
        </p>
      </main>
    );
  if (!job)
    return (
      <main className="page">
        <p className="empty">載入中…</p>
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
    { label: "重新轉錄…", disabled: !job.has_audio || active, onSelect: () => setRerunning(true) },
    ...(payload ? [{ label: "還原原始結果…", disabled: !payload.edited || active, onSelect: () => setConfirmReset(true) }] : []),
    ...(job.source === "youtube" && job.source_url ? [{ label: "在 YouTube 開啟", href: job.source_url, external: true }] : []),
    { sep: true },
    { label: "刪除…", danger: true, disabled: active, onSelect: () => setDeleting(true) },
  ];
  const exportItems = (): MenuItem[] =>
    FORMATS(timeline).map(([f, label]) => ({ label: `匯出 ${label}`, href: exportUrl(job.id, f, timeline) }));
  const mobileItems = (): MenuItem[] => [
    ...(payload
      ? [
          { header: "匯出" },
          ...exportItems(),
          { sep: true } as MenuItem,
          { label: "顯示時間碼", checked: timeline, onSelect: () => setTimeline(!timeline) },
          { label: "跟隨播放", checked: follow, onSelect: () => setFollow(!follow) },
          ...(showToggle
            ? [
                { header: "播放來源" },
                { label: "YouTube 影片", checked: media.useVideo, disabled: !media.videoOk, onSelect: () => setMode("video") },
                { label: "伺服器保留的音訊", checked: media.useAudio, onSelect: () => setMode("audio") },
              ]
            : []),
          { sep: true } as MenuItem,
        ]
      : []),
    ...actionItems(),
    { sep: true },
    { header: "外觀" },
    ...(["system", "light", "dark"] as ThemeChoice[]).map((c) => ({
      label: THEME_LABEL[c],
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
            <b>轉錄失敗：</b>
            {job.error}
          </p>
          {payload && <p>下面是上一次成功的結果。</p>}
          {job.has_audio && (
            <div className="row">
              <button type="button" className="btn" onClick={() => setRerunning(true)}>
                重新轉錄
              </button>
            </div>
          )}
        </div>
      )}
      {job.status === "canceled" && <p className="note">任務已取消。{job.has_audio && "可以用「⋯」裡的重新轉錄再跑一次。"}</p>}
      {!active && job.status === "done" && !job.has_transcript && (
        <p className="note">
          逐字稿已刪除。{job.has_audio ? "可以用其他模型或字典重新轉錄。" : "音檔也已刪除，無法重新轉錄。"}
        </p>
      )}
    </>
  );

  return (
    <PlayerContext.Provider value={store}>
      <main className={`result${payload ? "" : " no-transcript"}${media.useVideo && payload ? " with-video" : ""}`}>
        <header className="r-head">
          <Link className="back" to="/" aria-label="回到錄音">
            <Icon name="chevL" />
          </Link>
          <div className="titles">
            <h1 className="break">{job.filename}</h1>
            <p className="meta num">
              <span className="m-date">{dateTime(job.created_at)}</span>
              <span>{duration(job.duration)}</span>
              <span className="m-src">
                {captionLang ? `YouTube 人工字幕（${languageName(captionLang)}）` : modelLabel(models, job.model)}
              </span>
              <span className="m-spk">
                {job.diarize ? (job.num_speakers ? `指定 ${job.num_speakers} 位講者` : "自動判斷講者") : "不分講者"}
              </span>
              {job.source === "youtube" && job.yt_channel && <span className="m-ch">{job.yt_channel}</span>}
              {job.source === "youtube" && job.source_url && (
                <a className="m-yt" href={job.source_url} target="_blank" rel="noopener noreferrer">
                  在 YouTube 開啟
                </a>
              )}
              {payload?.edited && <span className="m-ed">已修改</span>}
            </p>
          </div>
          <div className="head-tools">
            {payload && (
              <Tick on={timeline} onChange={setTimeline} className="d-only">
                時間碼
              </Tick>
            )}
            {showToggle && (
              <div className="seg-toggle d-only" role="group" aria-label="播放來源">
                <button
                  type="button"
                  aria-pressed={media.useVideo}
                  disabled={!media.videoOk}
                  title={videoError ?? "用嵌入的 YouTube 影片播放"}
                  onClick={() => setMode("video")}
                >
                  影片
                </button>
                <button type="button" aria-pressed={media.useAudio} title="用伺服器保留的音訊播放" onClick={() => setMode("audio")}>
                  音訊
                </button>
              </div>
            )}
            {payload && (
              <MenuButton className="btn d-only" items={exportItems} aria-label="匯出">
                匯出
              </MenuButton>
            )}
            <MenuButton className="btn icon" items={isMobile ? mobileItems : actionItems} aria-label="更多動作">
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
              if (m === "transcript") setPayload(null);
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
            title="還原原始結果"
            message="所有改過的文字、講者與名稱都會回到轉錄完成時的樣子，這個動作無法復原。"
            confirmLabel="還原"
            danger
            onClose={() => setConfirmReset(false)}
            onConfirm={async () => {
              try {
                setPayload(await api.resetEdits(job.id));
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
            title="放棄修改？"
            message="這一句還沒儲存，離開這個頁面會放棄修改。"
            confirmLabel="離開"
            cancelLabel="繼續編輯"
            danger
            onClose={() => blocker.reset()}
            onConfirm={() => blocker.proceed()}
          />
        )}
      </main>
    </PlayerContext.Provider>
  );
}
