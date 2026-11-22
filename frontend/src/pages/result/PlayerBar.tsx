import { useLayoutEffect, useRef } from "react";
import Icon from "../../components/Icon";
import Tick from "../../components/Tick";
import { tc } from "../../format";
import { useT } from "../../i18n";
import { usePlayer, usePlayerStore } from "../../lib/player";
import { colorClass, segmentIndexAt, speakerName } from "../../lib/speakers";
import type { Transcript } from "../../types";

const RATES = [1, 1.25, 1.5, 2, 0.75];

/** 目前時間（只有這個元件每一幀重畫）。 */
function NowTime({ duration }: { duration: number }) {
  const time = usePlayer((s) => Math.floor(s.time));
  return (
    <div className="ptime num" aria-live="off">
      <b>{tc(time)}</b> <span>/ {tc(duration)}</span>
    </div>
  );
}

/** 正在播放的那一句（只在換段落時重畫）。 */
function NowLine({ t, labels }: { t: Transcript; labels: string[] }) {
  const tx = useT();
  const state = usePlayer((s) => (!s.playing && s.time < 0.05 ? -2 : segmentIndexAt(t.segments, s.time)));
  if (state === -2) return <div className="pnow"><span className="t">{tx("player.start")}</span></div>;
  if (state < 0) return <div className="pnow"><span className="t">{tx("player.silence")}</span></div>;
  const seg = t.segments[state];
  return (
    <div className="pnow">
      <i className={`sw ${colorClass(seg.speaker ? labels.indexOf(seg.speaker) : -1)}`} title={speakerName(t, seg.speaker)} />
      <span className="t">{seg.text}</span>
    </div>
  );
}

/** 桌機底部細長播放條：播放、時間、目前這一句、速度、上一句／下一句、跟隨播放。 */
export default function PlayerBar({
  t,
  labels,
  duration,
  canSeek,
  follow,
  onFollow,
}: {
  t: Transcript;
  labels: string[];
  duration: number;
  canSeek: boolean;
  follow: boolean;
  onFollow: (v: boolean) => void;
}) {
  const tx = useT();
  const store = usePlayerStore();
  const playing = usePlayer((s) => s.playing);
  const rate = usePlayer((s) => s.rate);
  const ready = usePlayer((s) => s.ready);
  const usable = canSeek && ready;
  const barRef = useRef<HTMLDivElement>(null);

  // 提示（toast）往上移到播放條上方 12px，不要蓋住目前這一句
  useLayoutEffect(() => {
    const root = document.documentElement;
    const update = () => root.style.setProperty("--toast-b", `${(barRef.current?.offsetHeight ?? 57) + 12}px`);
    update();
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("resize", update);
      root.style.removeProperty("--toast-b");
    };
  }, []);

  const prev = () => {
    const now = store.state.time;
    const i = segmentIndexAt(t.segments, now);
    if (i >= 0 && now - t.segments[i].start > 1.5) store.seek(t.segments[i].start);
    else {
      let j = i - 1;
      if (i < 0) for (let k = 0; k < t.segments.length && t.segments[k].start < now; k++) j = k;
      if (j >= 0) store.seek(t.segments[j].start);
      else store.seek(0);
    }
  };
  const next = () => {
    const now = store.state.time;
    const j = t.segments.findIndex((s) => s.start > now + 0.05);
    if (j >= 0) store.seek(t.segments[j].start);
  };

  return (
    <div className="pbar" id="player" tabIndex={-1} role="region" aria-label={tx("player.label")} ref={barRef}>
      <div className="pbar-in">
        <button
          type="button"
          className="pbtn"
          disabled={!usable}
          onClick={() => store.toggle()}
          aria-label={playing ? tx("player.pause") : tx("player.play")}
        >
          <Icon name={playing ? "pause" : "play"} />
        </button>
        <NowTime duration={duration} />
        {canSeek ? <NowLine t={t} labels={labels} /> : <div className="pnow"><span className="t">{tx("player.noAudio")}</span></div>}
        <div className="pctl">
          <button
            type="button"
            className="tbtn speed num"
            disabled={!usable}
            aria-label={tx("player.rate", { rate })}
            onClick={() => store.setRate(RATES[(RATES.indexOf(rate) + 1) % RATES.length])}
          >
            {rate}×
          </button>
          <button type="button" className="icon-btn" disabled={!usable} onClick={prev} aria-label={tx("player.prev")}>
            <Icon name="prev" />
          </button>
          <button type="button" className="icon-btn" disabled={!usable} onClick={next} aria-label={tx("player.next")}>
            <Icon name="next" />
          </button>
          <Tick on={follow} onChange={onFollow} disabled={!canSeek}>
            {tx("job.follow")}
          </Tick>
        </div>
      </div>
    </div>
  );
}
