import { createContext, useContext, useSyncExternalStore } from "react";
import type { Segment } from "../types";
import { segmentIndexAt } from "./speakers";

/** 實際出聲的媒體（伺服器保留的音訊或嵌入的 YouTube 影片）。 */
export interface MediaAdapter {
  play(): void;
  pause(): void;
  /** final=false：拖曳色帶中（YouTube 不向伺服器要新的片段，放開時再要一次） */
  seek(seconds: number, final?: boolean): void;
  setRate(rate: number): void;
}

/** 跳轉後這段時間內，媒體回報的時間跟目標差太多就當成跳轉前的舊值（YouTube 的 getCurrentTime() 會慢一點才更新）。 */
const SEEK_GUARD_MS = 800;
const SEEK_GUARD_TOLERANCE = 0.5;

export interface PlayerState {
  time: number;
  playing: boolean;
  rate: number;
  /** 有媒體可以播（音訊載入好了或 YouTube 播放器準備好了） */
  ready: boolean;
  /** 只聽某位講者（講者代號）；null＝全部 */
  only: string | null;
  /** 使用者跳轉的次數（暫停中跳轉時，逐字稿也要捲到那個位置） */
  seekSeq: number;
}

/**
 * 結果頁唯一的播放時間來源：講者色帶、泡泡、桌機播放條、手機播放面板、YouTube 播放器都讀寫這裡。
 * 媒體元件（AudioSource、YoutubePlayer）attach 後回報時間與播放狀態，畫面用 usePlayer(selector) 訂閱需要的部分。
 */
export class PlayerStore {
  private listeners = new Set<() => void>();
  private media: MediaAdapter | null = null;
  state: PlayerState = { time: 0, playing: false, rate: 1, ready: false, only: null, seekSeq: 0 };
  /** 最近一次跳轉的目標與時間（performance.now()），見 reportTime */
  private seekGuard: { target: number; at: number } | null = null;
  duration: number;
  segments: Segment[] = [];

  constructor(duration: number) {
    this.duration = duration;
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  private set(patch: Partial<PlayerState>) {
    this.state = { ...this.state, ...patch };
    for (const fn of this.listeners) fn();
  }

  attach(media: MediaAdapter) {
    this.media = media;
    media.setRate(this.state.rate);
    this.set({ ready: true });
  }

  detach(media: MediaAdapter) {
    if (this.media !== media) return;
    this.media = null;
    this.set({ ready: false, playing: false });
  }

  /** 跳轉後還在等媒體跟上：回傳 true 表示這次回報的時間是跳轉前的舊值，要忽略。 */
  private staleReport(t: number): boolean {
    const g = this.seekGuard;
    if (!g) return false;
    // 回報的時間已經到目標附近：媒體跟上了，之後照常接受（播放中時間會往前走，不能一直拿目標比）
    if (Math.abs(t - g.target) <= SEEK_GUARD_TOLERANCE) {
      this.seekGuard = null;
      return false;
    }
    if (performance.now() - g.at < SEEK_GUARD_MS) return true;
    this.seekGuard = null;
    return false;
  }

  /** 剛跳轉完（媒體可能還在回報舊時間）時，「只聽某位講者」先不判斷，避免每一幀重複跳轉。 */
  private seeking(): boolean {
    return !!this.seekGuard && performance.now() - this.seekGuard.at < SEEK_GUARD_MS;
  }

  private mediaSeek(time: number, final = true) {
    this.seekGuard = { target: time, at: performance.now() };
    this.media?.seek(time, final);
  }

  /** 媒體回報目前時間；「只聽某位講者」時落在別人的段落就跳到那位講者的下一段。 */
  reportTime(t: number) {
    if (this.staleReport(t)) return;
    const only = this.state.only;
    if (only && this.state.playing && this.media && !this.seeking()) {
      const i = segmentIndexAt(this.segments, t);
      const cur = i >= 0 ? this.segments[i] : null;
      if (!cur || cur.speaker !== only || t >= cur.end + 0.05) {
        const next = this.segments.find((s) => s.speaker === only && s.start > t - 0.01 && s !== cur);
        if (next) {
          this.mediaSeek(next.start);
          this.set({ time: next.start });
        } else {
          this.media.pause();
        }
        return;
      }
    }
    if (t !== this.state.time) this.set({ time: t });
  }

  reportPlaying(playing: boolean) {
    if (playing !== this.state.playing) this.set({ playing });
  }

  /** 跳轉；scrubbing＝拖曳色帶中（放開時要再呼叫一次 scrubbing=false）。 */
  seek(t: number, play = false, scrubbing = false) {
    const time = Math.min(this.duration, Math.max(0, t));
    this.mediaSeek(time, !scrubbing);
    this.set({ time, seekSeq: this.state.seekSeq + 1 });
    if (play) this.media?.play();
  }

  play() {
    if (this.state.time >= this.duration - 0.05) this.seek(0);
    // YouTube 在還沒開始播時，跳轉要等到按播放才真的執行：開始播的頭幾幀可能還回報舊時間，一樣忽略
    else this.seekGuard = { target: this.state.time, at: performance.now() };
    this.media?.play();
  }

  pause() {
    this.media?.pause();
  }

  toggle() {
    if (this.state.playing) this.pause();
    else this.play();
  }

  setRate(rate: number) {
    this.media?.setRate(rate);
    this.set({ rate });
  }

  setOnly(label: string | null) {
    this.set({ only: label });
    if (label && this.state.playing) this.reportTime(this.state.time);
  }

  /** 逐字稿換了（編輯、合併講者）：更新段落；「只聽」的講者已經不存在時自動清除。 */
  setSegments(segments: Segment[], labels: string[]) {
    this.segments = segments;
    if (this.state.only !== null && !labels.includes(this.state.only)) this.set({ only: null });
  }
}

export const PlayerContext = createContext<PlayerStore | null>(null);

export function usePlayerStore(): PlayerStore {
  const p = useContext(PlayerContext);
  if (!p) throw new Error("PlayerContext 沒有提供");
  return p;
}

/** 訂閱播放狀態的一部分；selector 回傳的值沒變就不重畫（例如泡泡只看目前段落索引）。 */
export function usePlayer<T>(selector: (s: PlayerState, store: PlayerStore) => T): T {
  const store = usePlayerStore();
  return useSyncExternalStore(store.subscribe, () => selector(store.state, store));
}
