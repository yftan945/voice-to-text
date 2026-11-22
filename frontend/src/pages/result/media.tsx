import { useEffect, useRef } from "react";
import type { MediaAdapter, PlayerStore } from "../../lib/player";

/**
 * 伺服器保留的音訊（隱藏的 <audio>）：attach 到播放器 store，播放中每一幀回報時間（色帶細線才會順）。
 * 從影片切換過來時接著播 store 目前的位置。
 */
export function AudioSource({ src, store, onError }: { src: string; store: PlayerStore; onError: () => void }) {
  const ref = useRef<HTMLAudioElement>(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    const a = ref.current;
    if (!a) return;
    let raf = 0;
    const adapter: MediaAdapter = {
      play: () => void a.play().catch(() => store.reportPlaying(false)),
      pause: () => a.pause(),
      seek: (t) => {
        a.currentTime = t;
      },
      setRate: (r) => {
        a.playbackRate = r;
      },
    };
    const loop = () => {
      store.reportTime(a.currentTime);
      raf = requestAnimationFrame(loop);
    };
    const onPlay = () => {
      store.reportPlaying(true);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(loop);
    };
    const onPause = () => {
      cancelAnimationFrame(raf);
      store.reportPlaying(false);
      store.reportTime(a.currentTime);
    };
    const onSeeked = () => store.reportTime(a.currentTime);
    const onMeta = () => {
      if (store.state.time > 0) a.currentTime = store.state.time;
      a.playbackRate = store.state.rate;
    };
    const onErr = () => onErrorRef.current();
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onPause);
    a.addEventListener("seeked", onSeeked);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("error", onErr);
    store.attach(adapter);
    return () => {
      cancelAnimationFrame(raf);
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onPause);
      a.removeEventListener("seeked", onSeeked);
      a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("error", onErr);
      a.pause();
      store.detach(adapter);
    };
  }, [store]);

  return <audio ref={ref} src={src} preload="metadata" hidden />;
}

/** IFrame Player API 用到的部分（https://developers.google.com/youtube/iframe_api_reference） */
interface YTPlayer {
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  playVideo(): void;
  pauseVideo(): void;
  setPlaybackRate(rate: number): void;
  getCurrentTime(): number;
  getPlayerState(): number;
  getIframe(): HTMLIFrameElement;
  destroy(): void;
}
interface YTNamespace {
  Player: new (
    el: HTMLElement,
    opts: {
      host?: string;
      videoId: string;
      width?: string;
      height?: string;
      playerVars?: Record<string, string | number>;
      events?: {
        onReady?: () => void;
        onError?: (e: { data: number }) => void;
        onStateChange?: (e: { data: number }) => void;
      };
    },
  ) => YTPlayer;
}
declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

// 播放器狀態（onStateChange 的 data、getPlayerState()）
const UNSTARTED = -1;
const ENDED = 0;
const PLAYING = 1;
const BUFFERING = 3;
const CUED = 5;
const API_TIMEOUT_MS = 15000;
let apiPromise: Promise<YTNamespace> | null = null;

/** 載入 IFrame Player API（整個頁面只載一次）。 */
function loadApi(): Promise<YTNamespace> {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (!apiPromise) {
    apiPromise = new Promise<YTNamespace>((resolve, reject) => {
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        prev?.();
        if (window.YT) resolve(window.YT);
      };
      const s = document.createElement("script");
      s.src = "https://www.youtube.com/iframe_api";
      s.async = true;
      s.onerror = () => reject(new Error("load"));
      document.head.appendChild(s);
      window.setTimeout(() => reject(new Error("timeout")), API_TIMEOUT_MS);
    }).catch((e) => {
      apiPromise = null; // 下次再試
      throw e;
    });
  }
  return apiPromise;
}

/** IFrame API 的錯誤碼 → 說明 */
function errorReason(code: number): string {
  if (code === 100) return "影片已被移除、設為私人或不存在";
  if (code === 101 || code === 150) return "影片擁有者不允許在其他網站嵌入播放";
  if (code === 153) return "YouTube 播放器設定錯誤（瀏覽器沒有送出來源網址）";
  if (code === 2) return "影片 ID 有誤";
  return "YouTube 播放器發生錯誤";
}

/**
 * 嵌入 YouTube 播放器（youtube-nocookie.com），attach 到播放器 store：播放中（含緩衝中）每一幀回報 getCurrentTime()。
 * 載入失敗或影片無法嵌入時呼叫 onError，由呼叫端改用保留的音訊播放。
 *
 * seekTo() 在還沒開始、已載入（cued）、播完（ended）的狀態下會自己開始播放：這些狀態下跳轉只記下目標時間，
 * 等使用者按播放時才真的跳過去；暫停中的 seekTo() 會維持暫停，直接跳。
 */
export function YoutubePlayer({
  videoId,
  title,
  store,
  onError,
}: {
  videoId: string;
  title: string;
  store: PlayerStore;
  onError: (reason: string) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const titleRef = useRef(title);
  titleRef.current = title;

  useEffect(() => {
    let alive = true;
    let player: YTPlayer | null = null;
    let adapter: MediaAdapter | null = null;
    let raf = 0;
    // 還不能直接 seekTo（會自己開始播）時記下的目標時間，按播放時才跳
    let pendingSeek: number | null = null;
    // YT.Player 會把元素換成 iframe：用自己建立的子元素，不讓 React 管理的節點被換掉
    const host = hostRef.current;
    const mount = document.createElement("div");
    host?.appendChild(mount);
    const loop = () => {
      if (!player || !alive) return;
      store.reportTime(player.getCurrentTime());
      raf = requestAnimationFrame(loop);
    };
    // 從音訊切換過來時接著播的位置
    const startAt = store.state.time;

    loadApi().then(
      (YT) => {
        if (!alive) return;
        player = new YT.Player(mount, {
          host: "https://www.youtube-nocookie.com",
          videoId,
          width: "100%",
          height: "100%",
          playerVars: { rel: 0, playsinline: 1, origin: window.location.origin, start: Math.floor(startAt) },
          events: {
            onReady: () => {
              if (!alive || !player) return;
              const p = player;
              try {
                // 讀屏軟體念 iframe 的 title（外層 div 的 aria-label 念不到）
                p.getIframe().title = `YouTube 影片：${titleRef.current}`;
              } catch {
                /* 舊版 API 沒有 getIframe */
              }
              adapter = {
                play: () => {
                  if (pendingSeek !== null) {
                    p.seekTo(pendingSeek, true);
                    pendingSeek = null;
                  }
                  p.playVideo();
                },
                pause: () => {
                  pendingSeek = null;
                  p.pauseVideo();
                },
                seek: (t, final = true) => {
                  const st = p.getPlayerState();
                  if (st === UNSTARTED || st === CUED || st === ENDED) {
                    // 這幾個狀態下 seekTo 會自己開始播：先記下來，按播放時才跳
                    pendingSeek = t;
                  } else {
                    // 播放中、緩衝中、暫停中（暫停中 seekTo 會維持暫停）
                    pendingSeek = null;
                    p.seekTo(t, final);
                  }
                },
                setRate: (r) => p.setPlaybackRate(r),
              };
              store.attach(adapter);
              // 播放器還沒準備好時點了泡泡或拖了色帶：補上那次跳轉（start 參數只到整數秒）
              if (Math.abs(store.state.time - startAt) > 0.5) adapter.seek(store.state.time);
            },
            onStateChange: (e) => {
              cancelAnimationFrame(raf);
              // 緩衝中也算播放中（按鈕維持「暫停」、色帶細線繼續走），不然跳轉時按鈕會閃一下
              const playing = e.data === PLAYING || e.data === BUFFERING;
              store.reportPlaying(playing);
              // 還沒真正跳過去（等按播放）時，播放器的時間是舊的，不回報
              if (player && pendingSeek === null && e.data !== UNSTARTED && e.data !== CUED) {
                store.reportTime(player.getCurrentTime());
              }
              if (playing) raf = requestAnimationFrame(loop);
            },
            onError: (e) => alive && onErrorRef.current(errorReason(e.data)),
          },
        });
      },
      () => alive && onErrorRef.current("無法載入 YouTube 播放器（可能是網路問題，或被瀏覽器的擋廣告外掛擋掉）"),
    );
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      if (adapter) store.detach(adapter);
      try {
        player?.destroy();
      } catch {
        /* 播放器還沒建好 */
      }
      // destroy 之後 iframe 可能還在（播放器還沒建好時），整個清空
      host?.replaceChildren();
    };
  }, [videoId, store]);

  return <div className="yt-frame" ref={hostRef} />;
}
