import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ConfirmModal } from "../../components/Modal";
import { useToast } from "../../components/Toast";
import { diffSpan, joinSeparator, PARAGRAPH_GAP, tc } from "../../format";
import { prefersReducedMotion, useFinePointer } from "../../hooks";
import { copyText } from "../../lib/clipboard";
import { usePlayer, usePlayerStore } from "../../lib/player";
import { avatarChar, colorClass, segmentIndexAt, speakerName } from "../../lib/speakers";
import type { Dictionary, EditOp, Segment, Transcript as T } from "../../types";
import BubbleMenu from "./BubbleMenu";
import DictSuggest from "./DictSuggest";

/** 兩段之間沒人說話超過這個秒數，就在泡泡之間放舞台指示「（無人聲 N 秒）」。 */
const SILENCE_MARK = 6;
const LONG_PRESS_MS = 480;
/** 長按觸發後（手指已放開）最多等這麼久，沒等到 click 就不再吃掉 */
const SWALLOW_MS = 1500;

interface Group {
  key: number;
  speaker: string | null;
  segs: Segment[];
  /** 這一組前面的靜默秒數（0＝不標） */
  gapBefore: number;
  /** 組內：段落 id → 前面的靜默秒數 */
  gaps: Map<number, number>;
}

function buildGroups(segments: Segment[]): Group[] {
  const out: Group[] = [];
  let prev: Segment | null = null;
  for (const s of segments) {
    const gap = prev ? s.start - prev.end : 0;
    const last = out[out.length - 1];
    if (!last || last.speaker !== s.speaker) {
      out.push({ key: s.id, speaker: s.speaker, segs: [s], gapBefore: gap >= SILENCE_MARK ? gap : 0, gaps: new Map() });
    } else {
      last.segs.push(s);
      if (gap >= SILENCE_MARK) last.gaps.set(s.id, gap);
    }
    prev = s;
  }
  return out;
}

const Silence = ({ sec }: { sec: number }) => (
  <div className="silence" role="separator" aria-label={`靜默 ${Math.round(sec)} 秒`}>
    （無人聲 {Math.round(sec)} 秒）
  </div>
);

/** 泡泡原地編輯：Enter 儲存（選字中不算）、Esc 取消；改動會回報給上層判斷要不要確認放棄。 */
function SegEditor({
  seg,
  onSave,
  onCancel,
  onDirty,
}: {
  seg: Segment;
  onSave: (text: string) => void;
  onCancel: () => void;
  onDirty: (dirty: boolean) => void;
}) {
  const [text, setText] = useState(seg.text);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => onDirty(text.trim() !== seg.text), [text, seg.text, onDirty]);
  useEffect(() => {
    const ta = ref.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  }, [text]);
  useEffect(() => {
    const ta = ref.current;
    if (!ta) return;
    ta.focus({ preventScroll: true });
    ta.setSelectionRange(ta.value.length, ta.value.length);
    ta.closest(".seg, .para-edit")?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, []);
  return (
    <div className="editor">
      <textarea
        ref={ref}
        className="edit-ta"
        value={text}
        rows={1}
        aria-label={`編輯 ${tc(seg.start)} 這一句`}
        onChange={(e) => setText(e.target.value.replace(/\n/g, ""))}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing || e.keyCode === 229) return; // 注音、拼音選字中
          if (e.key === "Enter") {
            e.preventDefault();
            if (text.trim()) onSave(text);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
      />
      <div className="edit-bar">
        <span className="edit-hint">
          <b>Enter</b> 儲存　<b>Esc</b> 取消
        </span>
        <button type="button" className="tbtn" onClick={onCancel}>
          取消
        </button>
        <button type="button" className="tbtn ink" disabled={!text.trim()} onClick={() => onSave(text)}>
          儲存
        </button>
      </div>
    </div>
  );
}

interface Handlers {
  play: (seg: Segment) => void;
  openMenu: (seg: Segment, anchor: HTMLElement, touch: boolean) => void;
  save: (seg: Segment, text: string) => void;
  cancelEdit: () => void;
  setDirty: (d: boolean) => void;
  closeSuggest: () => void;
  dictAdded: (d: Dictionary) => void;
  /** 長按（觸控）：回傳 true 表示這次 click 要吃掉 */
  pressStart: (seg: Segment, e: React.PointerEvent<HTMLElement>) => void;
  pressMove: (e: React.PointerEvent) => void;
  pressEnd: () => void;
  consumeClick: () => boolean;
  /** 鍵盤焦點落在這一句（roving tabindex：只有這一句可以用 Tab 進來） */
  focused: (seg: Segment) => void;
}

interface GroupProps {
  group: Group;
  name: string;
  avatar: string;
  color: string;
  timeline: boolean;
  activeId: number | null;
  /** 這一組裡可以用 Tab 進來的那一句（其他泡泡 tabIndex=-1，用 ↑↓ 移動） */
  tabId: number | null;
  editingId: number | null;
  menuId: number | null;
  suggest: { segId: number; wrong: string; correct: string } | null;
  edited: Set<number>;
  canSeek: boolean;
  fine: boolean;
  readOnly: boolean;
  dicts: Dictionary[];
  h: Handlers;
}

/** ↑↓（Home／End）在泡泡之間移動焦點：照畫面順序找上一個／下一個泡泡（時間碼開或關都適用）。 */
/**
 * 叫出泡泡選單前：泡泡上緣在 sticky 色帶（.band-wrap）底緣之上（被標題列或色帶蓋住）時，先把它捲到色帶下方，
 * 浮起來（.lifted，z-index 高於標題列）的泡泡才不會蓋到標題列上；選單也會對齊捲動後的位置。
 */
function revealBubble(anchor: HTMLElement) {
  // 沒有色帶時至少要在標題列下方
  const limit = Math.max(
    0,
    ...[".nav", ".band-wrap"].map((sel) => document.querySelector(sel)?.getBoundingClientRect().bottom ?? 0),
  );
  const top = anchor.getBoundingClientRect().top;
  if (top < limit) window.scrollBy({ top: top - limit - 6, behavior: "auto" });
}

function moveBubbleFocus(from: HTMLElement, key: string) {
  const thread = from.closest(".thread");
  if (!thread) return;
  const all = Array.from(thread.querySelectorAll<HTMLElement>("[data-seg][role='button']"));
  const i = all.indexOf(from);
  const j = key === "Home" ? 0 : key === "End" ? all.length - 1 : i + (key === "ArrowDown" ? 1 : -1);
  const el = all[j];
  if (!el || el === from) return;
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
}

/** 一位講者連續的發言：頭像（群組內 sticky）＋名字只出現一次，下面是泡泡。 */
const SpeakerGroup = memo(function SpeakerGroup(p: GroupProps) {
  const { group, timeline, h } = p;

  const bubbleProps = (seg: Segment, text: ReactNode) => ({
    "data-seg": seg.id,
    tabIndex: p.tabId === seg.id ? 0 : -1,
    role: "button",
    "aria-label": `${p.name}，${tc(seg.start)}：${seg.text}`,
    "aria-haspopup": "menu" as const,
    "aria-keyshortcuts": "Shift+F10",
    onClick: (e: React.MouseEvent) => {
      if (h.consumeClick()) return;
      if (e.button !== 0 || window.getSelection()?.toString()) return; // 正在選取文字（要複製）時不跳播
      if (p.canSeek) h.play(seg);
    },
    onFocus: (e: React.FocusEvent<HTMLElement>) => {
      if (e.target === e.currentTarget) h.focused(seg);
    },
    onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (p.canSeek) h.play(seg);
      } else if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
        e.preventDefault();
        h.openMenu(seg, e.currentTarget, false);
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Home" || e.key === "End") {
        e.preventDefault();
        moveBubbleFocus(e.currentTarget, e.key);
      }
    },
    onContextMenu: (e: React.MouseEvent<HTMLElement>) => {
      e.preventDefault();
      h.openMenu(seg, e.currentTarget, !p.fine);
    },
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => h.pressStart(seg, e),
    onPointerMove: h.pressMove,
    onPointerUp: h.pressEnd,
    onPointerCancel: h.pressEnd,
    children: text,
  });

  const suggestFor = (seg: Segment) =>
    p.suggest?.segId === seg.id && !p.readOnly ? (
      <DictSuggest dicts={p.dicts} initial={p.suggest} onClose={h.closeSuggest} onAdded={h.dictAdded} />
    ) : null;

  let body: ReactNode;
  if (timeline) {
    body = group.segs.map((seg, i) => {
      const gap = group.gaps.get(seg.id);
      const editing = p.editingId === seg.id;
      const pos = group.segs.length === 1 ? "only" : i === 0 ? "first" : i === group.segs.length - 1 ? "last" : "mid";
      return (
        <div key={seg.id} className="seg-wrap">
          {gap ? <Silence sec={gap} /> : null}
          <div className={`seg ${pos}${p.activeId === seg.id ? " now" : ""}${editing ? " editing" : ""}`}>
            {editing ? (
              <div className="bub">
                <SegEditor seg={seg} onSave={(text) => h.save(seg, text)} onCancel={h.cancelEdit} onDirty={h.setDirty} />
              </div>
            ) : (
              <div
                className={`bub${p.menuId === seg.id ? " lifted" : ""}${p.edited.has(seg.id) ? " edited" : ""}`}
                {...bubbleProps(seg, seg.text)}
              />
            )}
            {!editing && (
              <span className="seg-side">
                <button
                  type="button"
                  className="tc num"
                  tabIndex={-1}
                  disabled={!p.canSeek}
                  onClick={() => h.play(seg)}
                  aria-label={`從 ${tc(seg.start)} 開始播放`}
                >
                  {tc(seg.start)}
                </button>
                {p.fine && (
                  <button
                    type="button"
                    className="more"
                    tabIndex={-1}
                    aria-label="這一句的動作"
                    onClick={(e) => h.openMenu(seg, e.currentTarget.parentElement!.previousElementSibling as HTMLElement, false)}
                  >
                    <svg className="i fill" viewBox="0 0 24 24" aria-hidden="true">
                      <circle cx="5.5" cy="12" r="1.7" />
                      <circle cx="12" cy="12" r="1.7" />
                      <circle cx="18.5" cy="12" r="1.7" />
                    </svg>
                  </button>
                )}
              </span>
            )}
          </div>
          {suggestFor(seg)}
        </div>
      );
    });
  } else {
    // 時間碼關閉：同一位講者的話接成段落（與 TXT 匯出相同的接法），停頓超過 2 秒換行
    const parts: ReactNode[] = [];
    let prev: Segment | null = null;
    for (const seg of group.segs) {
      const text = seg.text.trim();
      if (!text) continue;
      if (prev) parts.push(seg.start - prev.end > PARAGRAPH_GAP ? <br key={`b${seg.id}`} /> : joinSeparator(prev.text.trim(), text));
      parts.push(
        <span
          key={seg.id}
          className={`tx${p.activeId === seg.id ? " now" : ""}${p.editingId === seg.id ? " editing-src" : ""}${p.menuId === seg.id ? " lifted" : ""}`}
          {...bubbleProps(seg, text)}
        />,
      );
      prev = seg;
    }
    const editingSeg = group.segs.find((s) => s.id === p.editingId);
    const suggestSeg = group.segs.find((s) => s.id === p.suggest?.segId);
    body = (
      <>
        <div className="bub para">{parts}</div>
        {editingSeg && (
          <div className="para-edit">
            <span className="tc num">{tc(editingSeg.start)}</span>
            <div className="bub">
              <SegEditor seg={editingSeg} onSave={(text) => h.save(editingSeg, text)} onCancel={h.cancelEdit} onDirty={h.setDirty} />
            </div>
          </div>
        )}
        {suggestSeg && suggestFor(suggestSeg)}
      </>
    );
  }

  return (
    <>
      {group.gapBefore > 0 && <Silence sec={group.gapBefore} />}
      <section className={`grp ${p.color}`} aria-label={p.name}>
        <span className="av" aria-hidden="true">
          {p.avatar}
        </span>
        <div className="grp-body">
          <h3 className="who">{p.name}</h3>
          {body}
        </div>
      </section>
    </>
  );
});

/**
 * 逐字稿：講者頭像＋泡泡，一律靠左，同一位講者連續發言合併成一組。
 * 點泡泡從那裡播放；桌機滑過出現「⋯」或按右鍵、手機長按，打開選單（編輯文字、換講者、從這裡播放、複製）。
 */
export default function Transcript({
  t,
  original,
  labels,
  timeline,
  follow,
  readOnly,
  canSeek,
  dicts,
  onDictUpdated,
  onEdit,
  onDirtyChange,
}: {
  t: T;
  original: T;
  labels: string[];
  timeline: boolean;
  follow: boolean;
  readOnly: boolean;
  canSeek: boolean;
  dicts: Dictionary[];
  onDictUpdated: (d: Dictionary) => void;
  onEdit: (op: EditOp) => Promise<boolean>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const store = usePlayerStore();
  const toast = useToast();
  const fine = useFinePointer();
  const [editing, setEditing] = useState<number | null>(null);
  const [dirty, setDirtyState] = useState(false);
  const [pendingDiscard, setPendingDiscard] = useState<(() => void) | null>(null);
  const [suggest, setSuggest] = useState<{ segId: number; wrong: string; correct: string } | null>(null);
  const [menu, setMenu] = useState<{ seg: Segment; anchor: HTMLElement; touch: boolean } | null>(null);
  const userScrollAt = useRef(0);
  const press = useRef<{ x: number; y: number; timer: number } | null>(null);
  const swallowClick = useRef(false);
  const swallowTimer = useRef(0);
  /** 長按已觸發、手指還沒放開 */
  const holding = useRef(false);
  // roving tabindex：上次有焦點的那一句（沒有的話用播放中的那一句，再不然第一句）
  const [focusId, setFocusId] = useState<number | null>(null);

  // 目前播放的段落：只在段落換了時重畫（還沒播放過時不標）
  const activeId = usePlayer((s) => {
    if (!canSeek || (!s.playing && s.time < 0.05)) return null;
    const i = segmentIndexAt(t.segments, s.time);
    return i >= 0 ? t.segments[i].id : null;
  });
  const playing = usePlayer((s) => s.playing);
  const seekSeq = usePlayer((s) => s.seekSeq);
  const lastSeek = useRef(seekSeq);

  const groups = useMemo(() => buildGroups(t.segments), [t.segments]);
  const edited = useMemo(() => {
    const orig = new Map(original.segments.map((s) => [s.id, s]));
    const out = new Set<number>();
    for (const s of t.segments) {
      const o = orig.get(s.id);
      if (!o || o.text !== s.text || o.speaker !== s.speaker) out.add(s.id);
    }
    return out;
  }, [t.segments, original.segments]);

  // 進入唯讀（開始重新轉錄）時收掉所有編輯中的狀態
  useEffect(() => {
    if (!readOnly) return;
    setEditing(null);
    setDirtyState(false);
    setPendingDiscard(null);
    setSuggest(null);
    setMenu(null);
  }, [readOnly]);

  useEffect(() => onDirtyChange(editing !== null && dirty), [editing, dirty, onDirtyChange]);
  // 卸載（換版本、刪除逐字稿）時不要留下「有未儲存的修改」
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => () => window.clearTimeout(swallowTimer.current), []);

  // 使用者自己捲動後 2.5 秒內不自動跟隨
  useEffect(() => {
    const on = () => {
      userScrollAt.current = Date.now();
    };
    window.addEventListener("wheel", on, { passive: true });
    window.addEventListener("touchmove", on, { passive: true });
    return () => {
      window.removeEventListener("wheel", on);
      window.removeEventListener("touchmove", on);
    };
  }, []);

  // 跟隨播放：目前段落跑出可視範圍（扣掉 sticky 色帶與底部播放條）時捲回來；
  // 暫停中用色帶或按鈕跳轉時也捲過去
  useEffect(() => {
    const sought = seekSeq !== lastSeek.current;
    lastSeek.current = seekSeq;
    if (activeId === null || !follow || editing !== null || menu) return;
    if (!sought && (!playing || Date.now() - userScrollAt.current < 2500)) return;
    const el = document.querySelector<HTMLElement>(`[data-seg="${activeId}"]`);
    if (!el) return;
    const r = el.getBoundingClientRect();
    // 上緣：固定的標題列與 sticky 色帶取實際底緣較低的那條（色帶還沒黏住時在更下面；手機結果頁頂列會捲走，不用算）
    const top =
      Math.max(
        document.querySelector(".nav")?.getBoundingClientRect().bottom ?? 0,
        document.querySelector(".band-wrap")?.getBoundingClientRect().bottom ?? 0,
      ) + 8;
    // 手機右下角的 YouTube 小視窗（沒收合時）也會擋住
    const bottom =
      Math.min(
        document.querySelector<HTMLElement>(".pbar, .panel")?.getBoundingClientRect().top ?? window.innerHeight,
        document.querySelector<HTMLElement>(".video-box.floating:not(.tucked)")?.getBoundingClientRect().top ?? Infinity,
      ) - 16;
    if (r.top < top || r.bottom > bottom) {
      // 跳轉（含拖曳色帶）時直接到位，播放中慢慢捲
      const behavior = sought || prefersReducedMotion() ? "auto" : "smooth";
      // 放在畫面約 1/3 高；矮螢幕（手機橫放）上方的固定列較高時，至少放在它們下方
      window.scrollTo({ top: window.scrollY + r.top - Math.max(window.innerHeight * 0.34, top), behavior });
    }
  }, [activeId, follow, playing, editing, menu, seekSeq]);

  /** 編輯中的段落有沒存的改動時先確認，確認放棄才執行 action。 */
  const guardDiscard = (action: () => void) => {
    if (editing !== null && dirty) setPendingDiscard(() => action);
    else action();
  };
  const closeEditor = () => {
    setDirtyState(false);
    setEditing(null);
  };

  // 給 memo 的群組元件用的穩定 handler（內部讀最新的狀態）
  const live = useRef({ guardDiscard, closeEditor, canSeek, readOnly, onEdit, onDictUpdated, toast });
  live.current = { guardDiscard, closeEditor, canSeek, readOnly, onEdit, onDictUpdated, toast };
  const h = useMemo<Handlers>(() => {
    /** ms 後不再吃掉 click；手指還按著時等放開後再算 */
    const armSwallow = (ms: number) => {
      window.clearTimeout(swallowTimer.current);
      swallowTimer.current = window.setTimeout(() => {
        if (holding.current) armSwallow(ms);
        else swallowClick.current = false;
      }, ms);
    };
    return {
      play: (seg) => {
        if (live.current.canSeek) store.seek(seg.start, true);
      },
      openMenu: (seg, anchor, touch) => {
        revealBubble(anchor);
        setMenu({ seg, anchor, touch });
      },
      save: async (seg, text) => {
        const v = text.trim();
        if (v === seg.text) {
          live.current.closeEditor();
          return;
        }
        if (await live.current.onEdit({ op: "set_text", segment_id: seg.id, text: v })) {
          live.current.closeEditor();
          const d = diffSpan(seg.text, v);
          if (d.wrong && d.correct) setSuggest({ segId: seg.id, ...d });
          else live.current.toast("已儲存");
        }
      },
      cancelEdit: () => live.current.guardDiscard(live.current.closeEditor),
      setDirty: (d) => setDirtyState(d),
      closeSuggest: () => setSuggest(null),
      dictAdded: (d) => live.current.onDictUpdated(d),
      pressStart: (seg, e) => {
        // 新的一次按下：上一次長按留下的「吃掉 click」不算數（Android 長按開選單後不會送 click，不清會吃掉下一次點擊）
        window.clearTimeout(swallowTimer.current);
        swallowClick.current = false;
        holding.current = false;
        if (e.pointerType === "mouse" || e.button > 0) return;
        const anchor = e.currentTarget;
        window.clearTimeout(press.current?.timer);
        press.current = {
          x: e.clientX,
          y: e.clientY,
          timer: window.setTimeout(() => {
            press.current = null;
            swallowClick.current = true;
            holding.current = true;
            // 放開後跟著來的 click 要吃掉；沒有 click（Android 長按）的話自動清掉
            armSwallow(SWALLOW_MS);
            navigator.vibrate?.(12);
            revealBubble(anchor);
            setMenu({ seg, anchor, touch: true });
          }, LONG_PRESS_MS),
        };
      },
      pressMove: (e) => {
        const p = press.current;
        if (p && Math.hypot(e.clientX - p.x, e.clientY - p.y) > 8) {
          window.clearTimeout(p.timer);
          press.current = null;
        }
      },
      pressEnd: () => {
        if (press.current) window.clearTimeout(press.current.timer);
        press.current = null;
        if (swallowClick.current) {
          // 長按後放開：click 會在很短的時間內送出（吃掉），之後就清掉
          holding.current = false;
          armSwallow(400);
        }
      },
      consumeClick: () => {
        const v = swallowClick.current;
        swallowClick.current = false;
        holding.current = false;
        window.clearTimeout(swallowTimer.current);
        return v;
      },
      focused: (seg) => setFocusId(seg.id),
    };
  }, [store]);

  const closeMenu = (refocus: boolean) => {
    const anchor = menu?.anchor;
    setMenu(null);
    if (refocus && anchor && document.contains(anchor)) anchor.focus({ preventScroll: true });
  };

  // 可以用 Tab 進來的那一句：上次有焦點的、播放中的、或第一句
  const tabId =
    focusId !== null && t.segments.some((s) => s.id === focusId)
      ? focusId
      : (activeId ?? t.segments.find((s) => s.text.trim())?.id ?? null);

  return (
    <article className={`thread${timeline ? "" : " plain"}`} aria-label="逐字稿">
      {groups.map((g) => {
        const idx = g.speaker ? labels.indexOf(g.speaker) : -1;
        const has = (id: number | null | undefined) => id != null && g.segs.some((s) => s.id === id);
        return (
          <SpeakerGroup
            key={g.key}
            group={g}
            name={speakerName(t, g.speaker)}
            avatar={g.speaker ? avatarChar(t.speakers[g.speaker]?.name || g.speaker, idx) : "?"}
            color={colorClass(idx)}
            timeline={timeline}
            activeId={has(activeId) ? activeId : null}
            tabId={has(tabId) ? tabId : null}
            editingId={has(editing) ? editing : null}
            menuId={has(menu?.seg.id) ? menu!.seg.id : null}
            suggest={has(suggest?.segId) ? suggest : null}
            edited={edited}
            canSeek={canSeek}
            fine={fine}
            readOnly={readOnly}
            dicts={dicts}
            h={h}
          />
        );
      })}
      {t.segments.length === 0 && <p className="empty">沒有辨識出任何文字。</p>}

      {menu && (
        <BubbleMenu
          anchor={menu.anchor}
          seg={menu.seg}
          t={t}
          labels={labels}
          readOnly={readOnly}
          canSeek={canSeek}
          touch={menu.touch}
          onClose={closeMenu}
          onEditText={() => {
            const seg = menu.seg;
            closeMenu(false);
            guardDiscard(() => {
              setSuggest(null);
              setDirtyState(false);
              setEditing(seg.id);
            });
          }}
          onPlay={() => {
            const seg = menu.seg;
            closeMenu(true);
            h.play(seg);
          }}
          onCopy={async () => {
            const seg = menu.seg;
            closeMenu(true);
            toast((await copyText(seg.text)) ? "已複製這一句" : "這個瀏覽器不允許複製，請手動選取文字");
          }}
          onSetSpeaker={async (label) => {
            const seg = menu.seg;
            closeMenu(true);
            if (await onEdit({ op: "set_speaker", segment_id: seg.id, speaker: label }))
              toast(`這一句改成「${speakerName(t, label)}」`);
          }}
          onNewSpeaker={async (name) => {
            const seg = menu.seg;
            const ok = await onEdit({ op: "set_speaker", segment_id: seg.id, speaker: null, new_name: name });
            if (ok) {
              closeMenu(true);
              toast(`這一句改成新講者「${name}」`);
            }
            return ok;
          }}
        />
      )}

      {pendingDiscard && (
        <ConfirmModal
          title="放棄修改？"
          message="這一句還沒儲存，要放棄修改嗎？"
          confirmLabel="放棄修改"
          cancelLabel="繼續編輯"
          danger
          onClose={() => setPendingDiscard(null)}
          onConfirm={() => {
            const action = pendingDiscard;
            setPendingDiscard(null);
            setDirtyState(false);
            setEditing(null);
            action();
          }}
        />
      )}
    </article>
  );
}
