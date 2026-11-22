import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, audioUrl } from "../../api";
import Icon from "../../components/Icon";
import { MenuButton, type MenuItem } from "../../components/Menu";
import { ConfirmModal, PromptModal } from "../../components/Modal";
import SpeakerBand from "../../components/SpeakerBand";
import { useToast } from "../../components/Toast";
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
export function mediaChoice(job: Job, mode: MediaMode, videoError: string | null) {
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
  videoError: string | null;
  onVideoError: (reason: string) => void;
  onDirtyChange: (dirty: boolean) => void;
  /** 伺服器回 409（任務排隊或重新轉錄中，不能編輯）：重新讀取任務、進入唯讀 */
  onBusy: () => void;
  /** 放在色帶上方的狀態區塊（處理中進度、失敗原因） */
  top?: ReactNode;
  /** 手機：上拉播放面板標題列的「⋯」選單（和結果頁標題列的「更多動作」同一份） */
  moreItems?: () => MenuItem[];
}) {
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
        <nav className="skip-links" aria-label="跳過逐字稿">
          <a href="#player" onClick={(e) => skipTo(e, "#player")}>
            跳到播放器
          </a>
          <a href={isMobile ? "#player" : "#speakers"} onClick={(e) => skipTo(e, isMobile ? "#player" : "#speakers")}>
            跳到講者清單
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
            YouTube 影片無法在這裡播放：{videoError}。{media.canPlay ? "已改用伺服器保留的音訊播放。" : "音檔也已刪除，無法播放。"}
          </p>
        )}
        {media.useAudio && audioError && <p className="note warn-note">音檔無法播放（可能是格式或網路問題）。</p>}
        {!media.canSeek && !media.videoId && <p className="note">音檔已刪除，只能閱讀與編輯逐字稿。</p>}

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
            {readOnly
              ? "重新轉錄中，完成前不能編輯。"
              : "點泡泡從那裡播放；滑過泡泡出現「⋯」，或按右鍵，可以編輯文字、換講者。鍵盤：↑↓ 切換泡泡、Shift+F10 開選單。"}
          </p>
        )}
        {isMobile && pressHint && !readOnly && (
          <p className="hint press-hint" role="status">
            <span>長按泡泡可以編輯文字、換講者。</span>
            <button type="button" className="tbtn ink" onClick={() => setPressHint(false)}>
              知道了
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

      <aside className={`r-side${media.useVideo ? " has-video" : ""}`} aria-label="影片與講者">
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
                aria-label={videoTucked ? "顯示影片" : "收合影片"}
                aria-expanded={!videoTucked}
              >
                {videoTucked ? "影片" : "收合"}
              </button>
            )}
          </div>
        )}
        {!isMobile && (
          <div className="side-speakers">
            <h2 id="speakers" tabIndex={-1}>
              講者
            </h2>
            {speakers}
            {labels.length > 0 && (
              <p className="side-note">改名會套用到這位講者的每一句。存成聲紋後，之後的錄音會自動認出這位講者。</p>
            )}
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
              <MenuButton className="icon-btn pmore" items={moreItems} aria-label="更多動作">
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
          title={voiceprintFor.voiceprintId !== null ? "更新聲紋" : "存成聲紋"}
          label="名稱"
          initial={t.speakers[voiceprintFor.label]?.name ?? ""}
          placeholder={speakerName(t, voiceprintFor.label)}
          confirmLabel={voiceprintFor.voiceprintId !== null ? "更新" : "存入聲紋庫"}
          description={
            <p>
              {voiceprintFor.voiceprintId !== null
                ? `用這份錄音裡「${speakerName(t, voiceprintFor.label)}」的聲音更新聲紋庫裡對應的那一筆（不會新增一筆）。`
                : `把「${speakerName(t, voiceprintFor.label)}」的聲音存進聲紋庫，之後轉錄會自動認出這個人。`}
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
            toast(r.voiceprint_updated ? `已更新聲紋：${name}` : `已存入聲紋庫：${name}`);
          }}
        />
      )}

      {merge && (
        <ConfirmModal
          title="合併講者"
          message={`把「${speakerName(t, merge.source)}」的 ${stats[merge.source]?.count ?? 0} 段併到「${speakerName(t, merge.target)}」？合併後「${speakerName(t, merge.source)}」會從講者清單消失，只能用「還原原始結果」整份復原。`}
          confirmLabel="合併"
          danger
          onClose={() => setMerge(null)}
          onConfirm={async () => {
            const target = speakerName(t, merge.target);
            if (await edit({ op: "merge_speakers", source: merge.source, target: merge.target })) toast(`已合併到「${target}」`);
            setMerge(null);
          }}
        />
      )}
    </>
  );
}
