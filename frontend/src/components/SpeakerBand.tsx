import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { tc } from "../format";
import type { PlayerStore } from "../lib/player";
import { colorClass, type Run } from "../lib/speakers";

function Runs({ runs, duration }: { runs: Run[]; duration: number }) {
  return (
    <div className="band-track">
      <div className="band-runs">
        {duration > 0 &&
          runs.map((r, i) => (
            <i
              key={i}
              className={colorClass(r.sp)}
              style={{ left: `${(r.s / duration) * 100}%`, width: `${((r.e - r.s) / duration) * 100}%` }}
            />
          ))}
      </div>
    </div>
  );
}

/** 首頁的迷你色帶：只畫，不互動。 */
export function MiniBand({ runs, duration, label }: { runs: Run[]; duration: number; label: string }) {
  return (
    <div className="band mini" role="img" aria-label={label} title={label}>
      <Runs runs={runs} duration={duration} />
    </div>
  );
}

const TICK_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

/**
 * 結果頁的講者色帶，兼任進度列：點擊或拖曳跳轉、←→ 前後 5 秒、PageUp／PageDown 30 秒、Home／End。
 * 播放位置是一條鈷藍細線，直接改 DOM（不經過 React 重畫）；載入時由左往右畫出（reduced-motion 直接出現）。
 */
export default function SpeakerBand({
  runs,
  duration,
  player,
  seekable,
  speakerAt,
  draw = false,
  ticks = true,
  className = "",
}: {
  runs: Run[];
  duration: number;
  player: PlayerStore;
  seekable: boolean;
  speakerAt: (t: number) => string | null;
  draw?: boolean;
  ticks?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [scrub, setScrub] = useState(false);
  const dragging = useRef(false);
  /** 拖曳中最後的位置：放開時再跳一次（YouTube 拖曳中不向伺服器要新片段，放開才要） */
  const lastScrub = useRef<number | null>(null);
  const endScrub = () => {
    if (dragging.current && lastScrub.current !== null) player.seek(lastScrub.current);
    dragging.current = false;
    lastScrub.current = null;
    setScrub(false);
  };

  // 播放位置：訂閱 store，直接更新細線位置與 aria
  useLayoutEffect(() => {
    const update = () => {
      const t = player.state.time;
      const el = ref.current;
      if (headRef.current) headRef.current.style.left = `${(Math.min(t, duration) / (duration || 1)) * 100}%`;
      if (el) {
        el.setAttribute("aria-valuenow", String(Math.round(t)));
        el.setAttribute("aria-valuetext", `${tc(t)}，共 ${tc(duration)}`);
      }
    };
    update();
    return player.subscribe(update);
  }, [player, duration]);

  useEffect(() => {
    const el = ref.current;
    if (!el || !ticks) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ticks]);

  const timeAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    return Math.min(duration, Math.max(0, ((clientX - r.left) / r.width) * duration));
  };
  const showTip = (clientX: number) => {
    const el = ref.current;
    const tip = tipRef.current;
    if (!el || !tip) return;
    const r = el.getBoundingClientRect();
    const t = timeAt(clientX);
    const who = speakerAt(t);
    tip.textContent = tc(t) + (who ? `　${who}` : "");
    tip.style.left = `${Math.min(r.width - 40, Math.max(40, clientX - r.left))}px`;
  };

  const step = TICK_STEPS.find((s) => (s / (duration || 1)) * (width || 600) >= 64) ?? 3600;
  const tickTimes: number[] = [];
  if (ticks && duration > 0) for (let t = 0; t < duration - step * 0.35; t += step) tickTimes.push(t);

  return (
    <>
      <div
        ref={ref}
        className={`band${seekable ? " seekable" : ""}${draw ? " draw" : ""}${scrub ? " scrub" : ""} ${className}`}
        role="slider"
        tabIndex={seekable ? 0 : -1}
        aria-label="講者色帶與播放位置"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-disabled={!seekable || undefined}
        onPointerDown={(e) => {
          if (!seekable || e.button > 0) return;
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          setScrub(true);
          showTip(e.clientX);
          lastScrub.current = timeAt(e.clientX);
          player.seek(lastScrub.current, false, true);
        }}
        onPointerMove={(e) => {
          if (!seekable) return;
          showTip(e.clientX);
          if (dragging.current) {
            lastScrub.current = timeAt(e.clientX);
            player.seek(lastScrub.current, false, true);
          }
        }}
        onPointerUp={endScrub}
        onPointerCancel={endScrub}
        onKeyDown={(e) => {
          if (!seekable) return;
          const d = ({ ArrowLeft: -5, ArrowRight: 5, ArrowDown: -5, ArrowUp: 5, PageDown: -30, PageUp: 30 } as Record<string, number>)[e.key];
          const t = player.state.time;
          if (d !== undefined) {
            e.preventDefault();
            player.seek(t + d);
          } else if (e.key === "Home") {
            e.preventDefault();
            player.seek(0);
          } else if (e.key === "End") {
            e.preventDefault();
            player.seek(duration - 0.01);
          }
        }}
      >
        <Runs runs={runs} duration={duration} />
        <div className="band-head" ref={headRef} />
        <div className="band-tip" ref={tipRef} aria-hidden="true" />
      </div>
      {ticks && (
        <div className="band-ticks" aria-hidden="true">
          {tickTimes.map((t) => (
            <span key={t} style={{ left: `${(t / duration) * 100}%` }}>
              {tc(t)}
            </span>
          ))}
          <span style={{ left: "100%" }}>{tc(duration)}</span>
        </div>
      )}
    </>
  );
}
