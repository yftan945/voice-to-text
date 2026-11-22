import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, audioUrl } from "../../api";
import Icon from "../../components/Icon";
import { MenuButton, type MenuItem } from "../../components/Menu";
import { ConfirmModal, PromptModal } from "../../components/Modal";
import SpeakerBand from "../../components/SpeakerBand";
import { useToast } from "../../components/Toast";
import { useT, type StrKey } from "../../i18n";
import type { PlayerStore } from "../../lib/player";
import { mergeRuns, segmentIndexAt, speakerLabels, speakerName, speakerStats } from "../../lib/speakers";
import type { Dictionary, EditOp, Job, TranscriptPayload } from "../../types";
import { AudioSource, YoutubePlayer } from "./media";
import PlayerBar from "./PlayerBar";
import PlayerPanel from "./PlayerPanel";
import SpeakerList from "./SpeakerList";
import Transcript from "./Transcript";

export type MediaMode = "video" | "audio";

const PRESS_HINT_KEY = "v2t.hint.longPress";

/** 跳過連結：不改網址（hash），直接把焦點移到目標（播放器、講者清單）。 */
function skipTo(e: React.MouseEvent, selector: string) {
  e.preventDefault();
  const el = document.querySelector<HTMLElement>(selector);
  if (!el) return;
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: "nearest" });
}

/** 這筆任務能用什麼播放：YouTube 影片（可嵌入時）或伺服器保留的音訊。 */
export function mediaChoice(job: Job, mode: MediaMode, videoError: StrKey | null) {
  const canPlay = job.has_audio && job.has_playback;
  const videoId = job.source === "youtube" ? job.video_id : null;
  const videoOk = !!videoId && !videoError;
  const useVideo = videoOk && (mode === "video" || !canPlay);
  const useAudio = canPlay && !useVideo;
  return { canPlay, videoId, videoOk, useVideo, useAudio, canSeek: useVideo || useAudio };
}

/**
 * 結果頁有逐字稿時的主體：sticky 講者色帶（兼任進度列）、逐字稿泡泡、講者側欄（桌機）、
 * 播放條（桌機）或上拉播放面板（手機）、YouTube 影片。播放狀態全部經過同一個 PlayerStore。
 */
export default function ResultView({
  job,
  payload,
  onChange,
  readOnly,
  timeline,
  follow,
  onFollow,
  isMobile,
  store,
  mode,
  videoError,
  onVideoError,
  onDirtyChange,
  onBusy,
  top,
  moreItems,
}: {
  job: Job;
  payload: TranscriptPayload;
  onChange: (p: TranscriptPayload) => void;
  readOnly: boolean;
  timeline: boolean;
  follow: boolean;
  onFollow: (v: boolean) => void;
  isMobile: boolean;
  store: PlayerStore;
  mode: MediaMode;
  /** 影片無法播放的原因（i18n key） */
  videoError: StrKey | null;
  onVideoError: (reason: StrKey) => void;
  onDirtyChange: (dirty: boolean) => void;
  /** 伺服器回 409（任務排隊或重新轉錄中，不能編輯）：重新讀取任務、進入唯讀 */
  onBusy: () => void;
  /** 放在色帶上方的狀態區塊（處理中進度、失敗原因） */
  top?: ReactNode;
  /** 手機：上拉播放面板標題列的「⋯」選單（和結果頁標題列的「更多動作」同一份） */
  moreItems?: () => MenuItem[];
}) {
  const tx = useT();
  const t = payload.current;
  const toast = useToast();
  const [error, setError] = useState("");
  const [audioError, setAudioError] = useState(false);
  const [dicts, setDicts] = useState<Dictionary[]>([]);
  const [merge, setMerge] = useState<{ source: string; target: string } | null>(null);
  const [voiceprintFor, setVoiceprintFor] = useState<{ label: string; voiceprintId: number | null } | null>(null);
  const [videoTucked, setVideoTucked] = useState(false);
  const [pressHint, setPressHint] = useState(false);

  const labels = useMemo(() => speakerLabels(t), [t]);
  const stats = useMemo(() => speakerStats(t), [t]);
  const duration = Math.max(t.duration || 0, job.duration || 0, t.segments[t.segments.length - 1]?.end ?? 0);
  const runs = useMemo(
    () =>
      mergeRuns(
        t.segments.filter((s) => s.speaker).map((s) => ({ s: s.start, e: s.end, sp: labels.indexOf(s.speaker!) })),
      ),
    [t.segments, labels],
  );
  const speakerAt = useCallback(
    (time: number) => {
      const i = segmentIndexAt(t.segments, time);
      return i >= 0 && time <= t.segments[i].end + 0.8 ? speakerName(t, t.segments[i].speaker) : null;
    },
    [t],
  );
  const media = mediaChoice(job, mode, videoError);
  const canSeek = media.canSeek && !(media.useAudio && audioError);

  useEffect(() => {
    api.dictionaries().then(setDicts, () => setDicts([]));
  }, []);

  // 播放器 store 讀最新的段落（只聽某位講者要用；那位講者被合併掉或重新轉錄後不存在了就自動清除）
  useEffect(() => {
    store.setSegments(t.segments, labels);
    store.duration = duration;
  }, [store, t.segments, labels, duration]);

  // 手機第一次看到結果頁：色帶下方提示一次「長按泡泡可以編輯」（看過就記在 localStorage）
  useEffect(() => {
    if (!isMobile || readOnly || t.segments.length === 0) return;
    try {
      if (localStorage.getItem(PRESS_HINT_KEY)) return;
      localStorage.setItem(PRESS_HINT_KEY, "1");
    } catch {
      return; // 讀寫不了（無痕模式等）就不提示，免得每次都出現
    }
    setPressHint(true);
  }, [isMobile, readOnly, t.segments.length]);

  // 進入唯讀（開始重新轉錄）時收掉講者相關的對話框
  useEffect(() => {
    if (!readOnly) return;
    setMerge(null);
    setVoiceprintFor(null);
  }, [readOnly]);

  // 空白鍵播放／暫停（焦點不在輸入框或按鈕上時）
  useEffect(() => {
    if (!canSeek) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.code !== "Space" || e.repeat || e.defaultPrevented) return;
      if (el !== document.body && !el.classList?.contains("thread")) return;
      e.preventDefault();
      store.toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canSeek, store]);

  const edit = useCallback(
    async (op: EditOp) => {
      setError("");
      try {
        onChange(await api.edit(job.id, op));
        return true;
      } catch (e) {
        setError((e as Error).message);
        toast((e as Error).message);
        if (e instanceof ApiError && e.status === 409) onBusy();
        return false;
      }
    },
    [job.id, onChange, toast, onBusy],
  );

  const speakers = (
    <SpeakerList
      t={t}
      labels={labels}
      stats={stats}
      voiceprintLabels={payload.voiceprint_speakers}
      readOnly={readOnly}
      onEdit={edit}
      onRequestMerge={(source, target) => setMerge({ source, target })}
      onRequestVoiceprint={(label) => setVoiceprintFor({ label, voiceprintId: t.speakers[label]?.voiceprint_id ?? null })}
    />
  );

  const warnings = t.warnings ?? [];

  return (
    <>
      <div className="r-main">
        <nav className="skip-links" aria-label={tx("result.skip")}>
          <a href="#player" onClick={(e) => skipTo(e, "#player")}>
            {tx("result.skipPlayer")}
          </a>
          <a href={isMobile ? "#player" : "#speakers"} onClick={(e) => skipTo(e, isMobile ? "#player" : "#speakers")}>
            {tx("result.skipSpeakers")}
          </a>
        </nav>
        {top}
        {error && (
          <p className="note error-note" role="alert">
            {error}
          </p>
        )}
        {warnings.length > 0 && (
          <div className="note warn-note">
            {warnings.map((w, i) => (
              <p key={i}>{w}</p>
            ))}
          </div>
        )}
        {media.videoId && videoError && (
          <p className="note warn-note" role="status">
            {tx("result.ytError", { reason: tx(videoError) })}
            {media.canPlay ? tx("result.ytFallback") : tx("result.ytNoAudio")}
          </p>
        )}
        {media.useAudio && audioError && <p className="note warn-note">{tx("result.audioError")}</p>}
        {!media.canSeek && !media.videoId && <p className="note">{tx("result.audioDeleted")}</p>}

        <div className="band-wrap">
          <SpeakerBand
            runs={runs}
            duration={duration}
            player={store}
            seekable={canSeek}
            speakerAt={speakerAt}
            draw
            ticks={!isMobile}
          />
        </div>
        {!isMobile && (
          <p className="hint">
            {readOnly ? tx("result.readOnly") : tx("result.hint")}
          </p>
        )}
        {isMobile && pressHint && !readOnly && (
          <p className="hint press-hint" role="status">
            <span>{tx("result.pressHint")}</span>
            <button type="button" className="tbtn ink" onClick={() => setPressHint(false)}>
              {tx("result.gotIt")}
            </button>
          </p>
        )}

        <Transcript
          t={t}
          original={payload.original}
          labels={labels}
          timeline={timeline}
          follow={follow}
          readOnly={readOnly}
          canSeek={canSeek}
          dicts={dicts}
          onDictUpdated={(d) => setDicts((prev) => prev.map((x) => (x.id === d.id ? d : x)))}
          onEdit={edit}
          onDirtyChange={onDirtyChange}
        />
      </div>

      <aside className={`r-side${media.useVideo ? " has-video" : ""}`} aria-label={tx("result.side")}>
        {media.useVideo && media.videoId && (
          <div className={`video-box${isMobile ? " floating" : ""}${isMobile && videoTucked ? " tucked" : ""}`}>
            <YoutubePlayer
              videoId={media.videoId}
              title={job.yt_title ?? job.filename}
              store={store}
              onError={onVideoError}
            />
            {isMobile && (
              <button
                type="button"
                className="video-tuck"
                onClick={() => setVideoTucked((v) => !v)}
                aria-label={videoTucked ? tx("result.showVideo") : tx("result.tuckVideo")}
                aria-expanded={!videoTucked}
              >
                {videoTucked ? tx("result.videoShort") : tx("result.tuck")}
              </button>
            )}
          </div>
        )}
        {!isMobile && (
          <div className="side-speakers">
            <h2 id="speakers" tabIndex={-1}>
              {tx("result.speakers")}
            </h2>
            {speakers}
            {labels.length > 0 && <p className="side-note">{tx("result.sideNote")}</p>}
          </div>
        )}
      </aside>

      {media.useAudio && <AudioSource src={audioUrl(job.id)} store={store} onError={() => setAudioError(true)} />}

      {isMobile ? (
        <PlayerPanel
          t={t}
          labels={labels}
          runs={runs}
          duration={duration}
          canSeek={canSeek}
          follow={follow}
          onFollow={onFollow}
          speakerAt={speakerAt}
          menu={
            moreItems && (
              <MenuButton className="icon-btn pmore" items={moreItems} aria-label={tx("job.more")}>
                <Icon name="more" />
              </MenuButton>
            )
          }
        >
          {speakers}
        </PlayerPanel>
      ) : (
        <PlayerBar t={t} labels={labels} duration={duration} canSeek={canSeek} follow={follow} onFollow={onFollow} />
      )}

      {voiceprintFor && (
        <PromptModal
          title={voiceprintFor.voiceprintId !== null ? tx("vpSave.updateTitle") : tx("vpSave.title")}
          label={tx("common.name")}
          initial={t.speakers[voiceprintFor.label]?.name ?? ""}
          placeholder={speakerName(t, voiceprintFor.label)}
          confirmLabel={voiceprintFor.voiceprintId !== null ? tx("vpSave.update") : tx("vpSave.save")}
          description={
            <p>
              {voiceprintFor.voiceprintId !== null
                ? tx("vpSave.updateMsg", { name: speakerName(t, voiceprintFor.label) })
                : tx("vpSave.saveMsg", { name: speakerName(t, voiceprintFor.label) })}
            </p>
          }
          onClose={() => setVoiceprintFor(null)}
          onSubmit={async (name) => {
            let r;
            try {
              r = await api.saveSpeakerVoiceprint(job.id, voiceprintFor.label, name, voiceprintFor.voiceprintId);
            } catch (e) {
              if (e instanceof ApiError && e.status === 409) {
                setVoiceprintFor(null);
                toast(e.message);
                onBusy();
                return;
              }
              throw e;
            }
            onChange(r);
            setVoiceprintFor(null);
            toast(r.voiceprint_updated ? tx("vpSave.updated", { name }) : tx("vpSave.saved", { name }));
          }}
        />
      )}

      {merge && (
        <ConfirmModal
          title={tx("merge.title")}
          message={tx("merge.msg", {
            src: speakerName(t, merge.source),
            n: stats[merge.source]?.count ?? 0,
            dst: speakerName(t, merge.target),
          })}
          confirmLabel={tx("merge.confirm")}
          danger
          onClose={() => setMerge(null)}
          onConfirm={async () => {
            const target = speakerName(t, merge.target);
            if (await edit({ op: "merge_speakers", source: merge.source, target: merge.target }))
              toast(tx("merge.done", { name: target }));
            setMerge(null);
          }}
        />
      )}
    </>
  );
}
