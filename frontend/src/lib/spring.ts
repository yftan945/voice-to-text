/**
 * 彈簧動畫與動量投射（C「對話串」的播放面板用）。
 * response＝無阻尼振盪週期（秒）、damping＝阻尼比；從目前位置與速度接續，可以中途改目標。
 */

/** Apple 的動量投射：放開時的速度（px/s）換算成慣性會再滑多遠。 */
export const project = (v: number) => ((v / 1000) * 0.998) / (1 - 0.998);

export interface SpringHandle {
  stop(): void;
}

export function spring(o: {
  from: number;
  to: number;
  velocity?: number;
  response?: number;
  damping?: number;
  onUpdate: (x: number) => void;
  onEnd?: () => void;
}): SpringHandle {
  const { from, to, velocity = 0, response = 0.35, damping = 1, onUpdate, onEnd } = o;
  const k = Math.pow((2 * Math.PI) / response, 2);
  const c = 2 * damping * Math.sqrt(k);
  let x = from;
  let v = velocity;
  let last = performance.now();
  let raf = 0;
  const frame = (now: number) => {
    const dt = Math.min((now - last) / 1000, 1 / 20);
    last = now;
    const n = Math.max(1, Math.ceil(dt * 240));
    const step = dt / n;
    for (let i = 0; i < n; i++) {
      v += (-k * (x - to) - c * v) * step;
      x += v * step;
    }
    if (Math.abs(x - to) < 0.002 * Math.max(1, Math.abs(to - from)) + 0.05 && Math.abs(v) < 4) {
      onUpdate(to);
      onEnd?.();
      return;
    }
    onUpdate(x);
    raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
  return { stop: () => cancelAnimationFrame(raf) };
}
