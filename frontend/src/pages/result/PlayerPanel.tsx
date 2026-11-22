import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon from "../../components/Icon";
import { popInert, pushInert } from "../../components/Modal";
import SpeakerBand from "../../components/SpeakerBand";
import Tick from "../../components/Tick";
import { tc } from "../../format";
import { prefersReducedMotion } from "../../hooks";
import { usePlayer, usePlayerStore } from "../../lib/player";
import { avatarChar, colorClass, segmentIndexAt, speakerName, type Run } from "../../lib/speakers";
import { project, spring, type SpringHandle } from "../../lib/spring";
import type { Transcript } from "../../types";

const MINI_H = 64;
const PANEL_SPEEDS = [1, 1.25, 1.5, 2];

/** 目前（或最近一段）講者的頭像＋名字＋時間。 */
function NowSpeaker({ t, labels, duration, big = false }: { t: Transcript; labels: string[]; duration: number; big?: boolean }) {
  const idx = usePlayer((s) => {
    const i = segmentIndexAt(t.segments, s.time);
    if (i >= 0) return i;
    // 空檔：顯示前一段的講者
    let j = -1;
    for (let k = 0; k < t.segments.length && t.segments[k].start <= s.time; k++) j = k;
    return Math.max(0, j);
  });
  const sec = usePlayer((s) => Math.floor(s.time));
  const seg = t.segments[idx];
  const li = seg?.speaker ? labels.indexOf(seg.speaker) : -1;
  return (
    <span className={`now-spk ${colorClass(li)}${big ? " big" : ""}`}>
      <span className="av" aria-hidden="true">
        {seg?.speaker ? avatarChar(t.speakers[seg.speaker]?.name || seg.speaker, li) : "▶"}
      </span>
      <span className="now-txt">
        <b>{seg ? speakerName(t, seg.speaker) : "逐字稿"}</b>
        <span className="num">
          {tc(sec)} / {tc(duration)}
        </span>
      </span>
    </span>
  );
}

/**
 * 手機的播放面板（C「對話串」）：底部常駐迷你播放條，往上拖或點一下用彈簧展開。
 * 拖曳 1:1 跟手、放開依速度投射判斷展開或收起、可以中途反向；reduced-motion 時改成淡入淡出。
 * 面板裡：大播放鍵、±15 秒、色帶、速度、跟隨播放、只聽某位講者、講者清單（children）。
 * 面板 render 到 body：展開時背景（#root）設 inert、面板當成 modal 對話框（跟 BottomSheet 一樣）。
 * menu：標題列右側的「⋯ 更多動作」（和結果頁標題列同一份選單；捲到中段時標題列已經捲走）。
 */
export default function PlayerPanel({
  t,
  labels,
  runs,
  duration,
  canSeek,
  follow,
  onFollow,
  speakerAt,
  menu,
  children,
}: {
  t: Transcript;
  labels: string[];
  runs: Run[];
  duration: number;
  canSeek: boolean;
  follow: boolean;
  onFollow: (v: boolean) => void;
  speakerAt: (t: number) => string | null;
  menu?: ReactNode;
  children: ReactNode;
}) {
  const store = usePlayerStore();
  const playing = usePlayer((s) => s.playing);
  const ready = usePlayer((s) => s.ready);
  const rate = usePlayer((s) => s.rate);
  const only = usePlayer((s) => s.only);
  const usable = canSeek && ready;
  const [expanded, setExpanded] = useState(false);

  const panelRef = useRef<HTMLElement>(null);
  const backRef = useRef<HTMLDivElement>(null);
  const progRef = useRef<HTMLElement>(null);
  const miniInfoRef = useRef<HTMLButtonElement>(null);
  const miniRef = useRef<HTMLDivElement>(null);
  const bigPlayRef = useRef<HTMLButtonElement>(null);
  const geo = useRef({ H: 600, cY: 536, y: 9999 });
  const springRef = useRef<SpringHandle | null>(null);
  const expandedRef = useRef(false);
  const dragged = useRef(false);

  const setY = useCallback((v: number) => {
    const g = geo.current;
    g.y = v;
    const p = Math.min(1, Math.max(0, 1 - v / g.cY));
    if (panelRef.current) {
      panelRef.current.style.transform = `translate3d(0,${v}px,0)`;
      panelRef.current.style.setProperty("--p", p.toFixed(4));
    }
    if (backRef.current) {
      backRef.current.style.opacity = p.toFixed(4);
      backRef.current.style.pointerEvents = p > 0.02 ? "auto" : "none";
    }
  }, []);

  const measure = useCallback(() => {
    const vh = window.innerHeight;
    let H = Math.min(Math.round(vh * 0.8), 700);
    H = Math.max(H, Math.min(440, vh - 40));
    // 迷你條高度含 iPhone 底部安全區（CSS 用 env() 加上去），直接量
    const mh = miniRef.current?.offsetHeight || MINI_H;
    geo.current.H = H;
    geo.current.cY = H - mh;
    document.documentElement.style.setProperty("--mini-h", `${mh}px`);
    panelRef.current?.style.setProperty("--H", `${H}px`);
    setY(expandedRef.current ? 0 : geo.current.cY);
  }, [setY]);

  useLayoutEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    document.documentElement.style.setProperty("--toast-b", `${MINI_H + 16}px`);
    return () => {
      window.removeEventListener("resize", measure);
      document.documentElement.style.removeProperty("--toast-b");
      document.documentElement.style.removeProperty("--mini-h");
      springRef.current?.stop();
    };
  }, [measure]);

  // 迷你條頂端的進度線：直接改 DOM
  useLayoutEffect(() => {
    const update = () => {
      if (progRef.current) progRef.current.style.transform = `scaleX(${duration ? store.state.time / duration : 0})`;
    };
    update();
    return store.subscribe(update);
  }, [store, duration]);

  const go = useCallback(
    (exp: boolean, v = 0, damping = 1) => {
      springRef.current?.stop();
      expandedRef.current = exp;
      setExpanded(exp);
      const to = exp ? 0 : geo.current.cY;
      if (prefersReducedMotion()) {
        // 減少動態：不位移，只淡入淡出
        setY(to);
        panelRef.current?.animate([{ opacity: 0.4 }, { opacity: 1 }], { duration: 150 });
        return;
      }
      springRef.current = spring({ from: geo.current.y, to, velocity: v, damping, onUpdate: setY });
    },
    [setY],
  );

  // 展開時背景 inert（讀屏軟體與 Tab 都只在面板裡）
  useLayoutEffect(() => {
    if (!expanded) return;
    pushInert();
    return () => popInert();
  }, [expanded]);

  // 展開／收起後的焦點：展開時迷你條 inert（焦點若在裡面會掉出來），焦點移到大播放鍵（不能播放時是收起鈕）；
  // 收起時等 render 拿掉迷你條的 inert 之後，再把焦點還給迷你條（焦點已經在面板外的別處就不搶）。
  // 觸控、拖曳、鍵盤展開都走這裡，不靠動畫時間猜
  const wasExpanded = useRef(false);
  useEffect(() => {
    const was = wasExpanded.current;
    wasExpanded.current = expanded;
    if (was === expanded) return;
    const panel = panelRef.current;
    const active = document.activeElement;
    const focusLost = !active || active === document.body;
    if (expanded) {
      const head = panel?.querySelector(".phead");
      const body = panel?.querySelector(".pscroll");
      if (!focusLost && (head?.contains(active) || body?.contains(active))) return;
      const big = bigPlayRef.current;
      (big && !big.disabled ? big : panel?.querySelector<HTMLElement>(".pclose"))?.focus({ preventScroll: true });
    } else if (focusLost || panel?.contains(active)) {
      miniInfoRef.current?.focus({ preventScroll: true });
    }
  }, [expanded]);

  // Esc 收起（面板裡的選單、對話框自己處理過的 Esc 不算）
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented && !document.querySelector(".modal-backdrop, .menu")) {
        go(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, go]);

  const rub = (raw: number) => {
    const { cY } = geo.current;
    return raw < 0 ? raw * 0.22 : raw > cY ? cY + (raw - cY) * 0.22 : raw;
  };

  /** 拖曳：事件掛在 document，手指拖出元件也不會斷；放開依速度投射判斷展開或收起。 */
  const onDragStart = (e: React.PointerEvent) => {
    if (e.button > 0) return;
    const id = e.pointerId;
    const sy = e.clientY;
    const y0 = geo.current.y;
    const onBtn = !!(e.target as HTMLElement).closest("button:not(.mini-info)");
    let moved = false;
    const samples: [number, number][] = [[e.timeStamp, y0]];
    dragged.current = false;
    springRef.current?.stop();
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      const dy = ev.clientY - sy;
      if (!moved && Math.abs(dy) < 6) return;
      if (!moved) {
        moved = true;
        dragged.current = true;
      }
      setY(rub(y0 + dy));
      samples.push([ev.timeStamp, geo.current.y]);
      while (samples.length > 2 && ev.timeStamp - samples[0][0] > 100) samples.shift();
    };
    const end = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", end);
      document.removeEventListener("pointercancel", end);
      if (!moved) {
        if (ev.type === "pointerup" && !onBtn) go(!expandedRef.current);
        else go(expandedRef.current);
        return;
      }
      // 拖曳後跟著來的 click（滑鼠拖曳時同一個 task 內就會送出）吃掉之後就清掉；
      // 觸控拖曳通常不會送 click，不清的話下一次點面板裡的按鈕會被吃掉
      window.setTimeout(() => {
        dragged.current = false;
      }, 0);
      const a = samples[0];
      const b = samples[samples.length - 1];
      const v = b[0] > a[0] ? ((b[1] - a[1]) / (b[0] - a[0])) * 1000 : 0;
      go(geo.current.y + project(v) < geo.current.cY / 2, v, Math.abs(v) > 500 ? 0.8 : 1);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", end);
    document.addEventListener("pointercancel", end);
  };

  return createPortal(
    <>
      <div className="pback" ref={backRef} onPointerDown={() => go(false)} aria-hidden="true" />
      <section
        className={`panel${expanded ? " open" : ""}`}
        ref={panelRef}
        id="player"
        tabIndex={-1}
        aria-label="播放器"
        role={expanded ? "dialog" : undefined}
        aria-modal={expanded ? true : undefined}
        onPointerDownCapture={() => {
          // 新的一次按下：上一次拖曳留下的旗標不算數
          dragged.current = false;
        }}
        onClickCapture={(e) => {
          // 拖曳後跟著來的 click 不要觸發按鈕
          if (dragged.current) {
            e.stopPropagation();
            e.preventDefault();
            dragged.current = false;
          }
        }}
      >
        {/* 展開時迷你條 inert：讀屏軟體與 Tab 不會走到被蓋住的迷你條 */}
        <div className="pmini" ref={miniRef} onPointerDown={onDragStart} inert={expanded}>
          <div className="mini-prog" aria-hidden="true">
            <i ref={progRef} />
          </div>
          <div className="mini-row">
            <button
              type="button"
              ref={miniInfoRef}
              className="mini-info"
              aria-expanded={expanded}
              aria-controls="panel-body"
              aria-label="展開播放面板"
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  go(true);
                }
              }}
            >
              <NowSpeaker t={t} labels={labels} duration={duration} />
            </button>
            <button
              type="button"
              className="play-btn"
              disabled={!usable}
              onClick={() => store.toggle()}
              aria-label={playing ? "暫停" : "播放"}
            >
              <Icon name={playing ? "pause" : "play"} size={24} />
            </button>
          </div>
        </div>
        <div className="phead" onPointerDown={onDragStart} inert={!expanded}>
          <i className="grab" aria-hidden="true" />
          {menu}
          <button
            type="button"
            className="icon-btn pclose"
            aria-label="收起播放面板"
            onClick={() => go(false)}
          >
            <Icon name="chevD" size={22} />
          </button>
        </div>
        <div className="pscroll" id="panel-body" inert={!expanded}>
          <NowSpeaker t={t} labels={labels} duration={duration} big />
          <div className="panel-band">
            <SpeakerBand runs={runs} duration={duration} player={store} seekable={usable} speakerAt={speakerAt} ticks={false} />
          </div>
          {!canSeek && <p className="help">音檔已刪除，無法播放。</p>}
          <div className="transport">
            <button type="button" className="icon-btn skip" disabled={!usable} onClick={() => store.seek(store.state.time - 15)} aria-label="倒退 15 秒">
              <Icon name="back15" size={32} />
            </button>
            <button
              type="button"
              ref={bigPlayRef}
              className="play-btn big"
              disabled={!usable}
              onClick={() => store.toggle()}
              aria-label={playing ? "暫停" : "播放"}
            >
              <Icon name={playing ? "pause" : "play"} size={32} />
            </button>
            <button type="button" className="icon-btn skip" disabled={!usable} onClick={() => store.seek(store.state.time + 15)} aria-label="快轉 15 秒">
              <Icon name="fwd15" size={32} />
            </button>
          </div>

          <h3 id="speed-h">播放速度</h3>
          <div className="speed-ctl" role="radiogroup" aria-labelledby="speed-h">
            {PANEL_SPEEDS.map((r) => (
              <button
                key={r}
                type="button"
                role="radio"
                aria-checked={rate === r}
                disabled={!usable}
                className="num"
                onClick={() => store.setRate(r)}
              >
                {r}×
              </button>
            ))}
          </div>
          <Tick on={follow} onChange={onFollow} disabled={!canSeek} className="panel-follow">
            跟隨播放<small>播放時自動捲到正在說的那一句</small>
          </Tick>

          {(labels.length > 1 || only !== null) && (
            <>
              <h3>只聽某位講者</h3>
              <div className="chips">
                <button type="button" className="chip all" aria-pressed={only === null} onClick={() => store.setOnly(null)}>
                  全部講者
                </button>
                {labels.map((l, i) => (
                  <button
                    key={l}
                    type="button"
                    className={`chip ${colorClass(i)}`}
                    aria-pressed={only === l}
                    disabled={!usable}
                    onClick={() => store.setOnly(l)}
                  >
                    <span className="av" aria-hidden="true">
                      {avatarChar(t.speakers[l]?.name || l, i)}
                    </span>
                    {speakerName(t, l)}
                  </button>
                ))}
              </div>
            </>
          )}

          <h3>講者</h3>
          {children}
        </div>
      </section>
    </>,
    document.body,
  );
}
