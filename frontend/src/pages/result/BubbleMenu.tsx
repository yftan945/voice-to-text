import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "../../components/Icon";
import { avatarChar, colorClass, speakerName } from "../../lib/speakers";
import type { Segment, Transcript } from "../../types";

/**
 * 泡泡選單（桌機滑過的「⋯」、右鍵；手機長按）：編輯文字、換講者（一排頭像加「＋新講者」）、從這裡播放、複製。
 * 手機打開時背後有淡淡的遮罩、泡泡浮起來。焦點進到第一項，Esc 關閉（換講者時先回上一層），關閉後焦點回到泡泡。
 */
export default function BubbleMenu({
  anchor,
  seg,
  t,
  labels,
  readOnly,
  canSeek,
  touch,
  onClose,
  onEditText,
  onPlay,
  onCopy,
  onSetSpeaker,
  onNewSpeaker,
}: {
  anchor: HTMLElement;
  seg: Segment;
  t: Transcript;
  labels: string[];
  readOnly: boolean;
  canSeek: boolean;
  touch: boolean;
  onClose: (refocus: boolean) => void;
  onEditText: () => void;
  onPlay: () => void;
  onCopy: () => void;
  onSetSpeaker: (label: string) => void;
  onNewSpeaker: (name: string) => Promise<boolean>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"main" | "speaker" | "new">("main");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const modeRef = useRef(mode);
  modeRef.current = mode;

  /** 「＋新講者」：焦點放到名稱輸入框；換講者：目前的講者；其他：第一項 */
  const focusFirst = () => {
    const m = ref.current;
    if (!m) return;
    const first =
      (modeRef.current === "new" ? m.querySelector<HTMLElement>(".pick-new input") : null) ??
      m.querySelector<HTMLElement>("[aria-pressed='true']") ??
      m.querySelector<HTMLElement>("input, button:not([disabled])");
    first?.focus({ preventScroll: true });
  };

  // 依泡泡位置擺放：放得下就在下方，否則在上方；手機的底部播放條要避開
  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    const r = anchor.getBoundingClientRect();
    const bottomBar = document.querySelector<HTMLElement>(".pbar, .panel")?.getBoundingClientRect().top ?? window.innerHeight;
    const limit = Math.min(window.innerHeight, bottomBar) - 8;
    const mw = m.offsetWidth;
    const mh = m.offsetHeight;
    const left = Math.min(Math.max(8, r.left), window.innerWidth - mw - 8);
    let top = r.bottom + 8;
    if (top + mh > limit) top = r.top - mh - 8;
    if (top < 8) top = Math.max(8, limit - mh);
    m.style.left = `${left}px`;
    m.style.top = `${top}px`;
    // 下一個 frame 再聚焦：手機長按時選單是在手指還按著的時候打開的，同一輪事件裡焦點會被泡泡拿回去
    const raf = requestAnimationFrame(focusFirst);
    return () => cancelAnimationFrame(raf);
  }, [anchor, mode]);

  // 手機長按：放開手指後瀏覽器補發的 mousedown 會把焦點移到泡泡上，這時拉回選單（Esc、方向鍵才有作用）
  useEffect(() => {
    if (!touch) return;
    const onFocusIn = (e: FocusEvent) => {
      if (anchor.contains(e.target as Node)) focusFirst();
    };
    document.addEventListener("focusin", onFocusIn);
    return () => document.removeEventListener("focusin", onFocusIn);
  }, [touch, anchor]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) closeRef.current(false);
    };
    const onResize = () => closeRef.current(false);
    // 保險：焦點不在選單裡時（例如被別的東西拿走）Esc 也能關閉；在選單裡的 Esc 由 onKey 處理
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented && !ref.current?.contains(document.activeElement)) {
        e.preventDefault();
        closeRef.current(true);
      }
    };
    document.addEventListener("pointerdown", onDown, true);
    window.addEventListener("resize", onResize);
    window.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("keydown", onEsc);
    };
  }, [anchor]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (mode === "main") closeRef.current(true);
      else setMode(mode === "new" ? "speaker" : "main");
      return;
    }
    if (e.key === "Tab") {
      // 選單內循環
      const items = Array.from(ref.current?.querySelectorAll<HTMLElement>("button:not([disabled]), input") ?? []);
      if (!items.length) return;
      const i = items.indexOf(document.activeElement as HTMLElement);
      if (e.shiftKey && i <= 0) {
        e.preventDefault();
        items[items.length - 1].focus();
      } else if (!e.shiftKey && i === items.length - 1) {
        e.preventDefault();
        items[0].focus();
      }
      return;
    }
    if (mode === "new") return;
    const keysNext = mode === "speaker" ? ["ArrowRight", "ArrowDown"] : ["ArrowDown"];
    const keysPrev = mode === "speaker" ? ["ArrowLeft", "ArrowUp"] : ["ArrowUp"];
    if (![...keysNext, ...keysPrev].includes(e.key)) return;
    e.preventDefault();
    const btns = Array.from(ref.current?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? []);
    const i = btns.indexOf(document.activeElement as HTMLElement);
    const d = keysNext.includes(e.key) ? 1 : -1;
    btns[(i + d + btns.length) % btns.length]?.focus();
  };

  const submitNew = async () => {
    const v = name.trim();
    if (!v || busy) return;
    setBusy(true);
    if (!(await onNewSpeaker(v))) setBusy(false);
  };

  return createPortal(
    <>
      {touch && <div className="ctx-scrim" aria-hidden="true" />}
      <div
        className={`menu ctx${mode !== "main" ? " pick-mode" : ""}`}
        ref={ref}
        role={mode === "main" ? "menu" : "dialog"}
        aria-label={mode === "main" ? "這一句的動作" : "換講者"}
        onKeyDown={onKey}
      >
        {mode === "main" ? (
          <>
            <button type="button" role="menuitem" className="mi" disabled={readOnly} onClick={onEditText}>
              <Icon name="edit" />
              編輯文字
            </button>
            <button
              type="button"
              role="menuitem"
              className="mi"
              disabled={readOnly || labels.length === 0}
              onClick={() => setMode("speaker")}
            >
              <Icon name="swap" />
              換講者
            </button>
            <button type="button" role="menuitem" className="mi" disabled={!canSeek} onClick={onPlay}>
              <Icon name="play" />
              從這裡播放
            </button>
            <button type="button" role="menuitem" className="mi" onClick={onCopy}>
              <Icon name="copy" />
              複製
            </button>
          </>
        ) : (
          <div className="pick">
            <div className="mh">這句話是誰說的</div>
            <div className="pick-row">
              {labels.map((l, i) => {
                const nm = speakerName(t, l);
                return (
                  <button
                    type="button"
                    key={l}
                    className={`pick-btn ${colorClass(i)}`}
                    aria-pressed={l === seg.speaker}
                    title={nm}
                    onClick={() => (l === seg.speaker ? closeRef.current(true) : onSetSpeaker(l))}
                  >
                    <span className="av" aria-hidden="true">
                      {avatarChar(t.speakers[l]?.name || l, i)}
                    </span>
                    <span className="pick-name">{nm}</span>
                  </button>
                );
              })}
              <button type="button" className="pick-btn new" aria-pressed={mode === "new"} onClick={() => setMode("new")}>
                <span className="av" aria-hidden="true">
                  <Icon name="plus" />
                </span>
                <span className="pick-name">新講者</span>
              </button>
            </div>
            {mode === "new" && (
              <form
                className="pick-new"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submitNew();
                }}
              >
                <input
                  className="field"
                  value={name}
                  placeholder="新講者名稱，例如：Alex"
                  aria-label="新講者名稱"
                  onChange={(e) => setName(e.target.value)}
                />
                <button type="submit" className="btn primary" disabled={!name.trim() || busy}>
                  建立
                </button>
              </form>
            )}
          </div>
        )}
      </div>
    </>,
    document.body,
  );
}
