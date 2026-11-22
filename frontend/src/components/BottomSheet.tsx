import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { popInert, pushInert } from "./Modal";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * 手機的底部面板（首頁的「貼上網址」「設定」）：一直掛著，關閉時 inert，裡面的輸入內容不會因為關閉而消失。
 * 打開時背景 inert、焦點進到面板（initialFocus 選擇器指定的元素）、Tab 在面板內循環、Esc 或點背景關閉，關閉後焦點回到打開它的按鈕。
 */
export default function BottomSheet({
  open,
  title,
  onClose,
  initialFocus,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  initialFocus?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useLayoutEffect(() => {
    if (!open) return;
    trigger.current = document.activeElement as HTMLElement | null;
    pushInert();
    return () => {
      popInert();
      const t = trigger.current;
      if (t && t !== document.body && document.contains(t)) t.focus({ preventScroll: true });
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const sheet = ref.current;
    // 等滑入動畫開始後再聚焦，手機鍵盤才不會把面板頂歪
    const timer = window.setTimeout(() => {
      const el = (initialFocus && sheet?.querySelector<HTMLElement>(initialFocus)) || sheet?.querySelector<HTMLElement>(FOCUSABLE);
      el?.focus({ preventScroll: true });
    }, 120);
    const onKey = (e: KeyboardEvent) => {
      if (!sheet) return;
      if (e.key === "Escape") {
        if (e.defaultPrevented) return;
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(sheet.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !sheet.contains(document.activeElement))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !sheet.contains(document.activeElement))) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, initialFocus]);

  return createPortal(
    <>
      <div className={`sheet-scrim${open ? " show" : ""}`} onClick={onClose} aria-hidden="true" />
      <div
        className={`sheet${open ? " open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
        inert={!open}
      >
        <div className="sheet-head">
          <h2>{title}</h2>
          <button type="button" className="tbtn ink" onClick={onClose}>
            關閉
          </button>
        </div>
        {children}
      </div>
    </>,
    document.body,
  );
}
