import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** 打開時焦點放哪：first＝第一個按鈕（預設）；close＝右上角關閉鈕（例如刪除對話框，避免誤按） */
  initialFocus?: "first" | "close";
  /** 內容較多的對話框（播放清單、重新轉錄）用較寬的版面 */
  wide?: boolean;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// 同時開著的對話框／底部面板數量：全部關掉才解除背景（#root）的 inert
let openCount = 0;

/** 打開一層蓋在畫面上的東西（對話框、底部面板）：背景 #root 設為 inert。 */
export function pushInert() {
  const root = document.getElementById("root");
  openCount += 1;
  if (root) root.inert = true;
}

export function popInert() {
  const root = document.getElementById("root");
  openCount = Math.max(0, openCount - 1);
  if (root && openCount === 0) root.inert = false;
}

/**
 * 對話框：render 到 body，打開時背景（#root）加 inert；Tab 只在對話框內循環；
 * 關閉後焦點回到打開它的按鈕。
 */
export default function Modal({ title, onClose, children, footer, initialFocus = "first", wide = false }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  // 在第一次 render 時就記下觸發的按鈕：內容裡的 autoFocus 會在 effect 之前搶走焦點
  const [trigger] = useState(() => document.activeElement as HTMLElement | null);

  // 背景 inert ＋ 關閉後還原焦點（只在掛載／卸載時做一次）
  useLayoutEffect(() => {
    pushInert();
    return () => {
      popInert();
      if (trigger && trigger !== document.body && document.contains(trigger)) trigger.focus();
    };
  }, [trigger]);

  // 初始焦點：內容裡有 autoFocus 的輸入框就用它，否則第一個按鈕（或關閉鈕）
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || dialog.contains(document.activeElement)) return;
    if (initialFocus === "close") {
      closeRef.current?.focus();
      return;
    }
    const first = Array.from(dialog.querySelectorAll<HTMLElement>(".modal-body button:not([disabled]), .modal-foot button:not([disabled])"))[0];
    (first ?? closeRef.current)?.focus();
  }, [initialFocus]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      if (e.key === "Escape") {
        // 已被處理過的 Esc（例如在段落編輯框按 Esc 才打開這個確認框）不要再把對話框關掉
        if (e.defaultPrevented) return;
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !dialog.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !dialog.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={dialogRef}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="關閉" ref={closeRef}>
            <Icon name="close" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

/** 確認對話框（取代 window.confirm）。 */
export function ConfirmModal({
  title,
  message,
  confirmLabel = "確定",
  cancelLabel = "取消",
  danger = false,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<unknown>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      await onConfirm();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {cancelLabel}
          </button>
          <button type="button" className={`btn ${danger ? "danger solid" : "primary"}`} disabled={busy} onClick={confirm}>
            {busy ? "處理中…" : confirmLabel}
          </button>
        </>
      }
    >
      <div>{typeof message === "string" ? <p>{message}</p> : message}</div>
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}

/** 輸入一個名稱的對話框（取代 window.prompt）。onSubmit 丟出錯誤時顯示在對話框裡。 */
export function PromptModal({
  title,
  label,
  description,
  initial = "",
  placeholder,
  confirmLabel = "確定",
  onSubmit,
  onClose,
}: {
  title: string;
  label: string;
  description?: ReactNode;
  initial?: string;
  placeholder?: string;
  confirmLabel?: string;
  onSubmit: (value: string) => void | Promise<unknown>;
  onClose: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const v = value.trim();
    if (!v || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit(v);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn primary" disabled={busy || !value.trim()} onClick={() => submit()}>
            {busy ? "處理中…" : confirmLabel}
          </button>
        </>
      }
    >
      {description && <div>{description}</div>}
      <form onSubmit={submit}>
        <label className="lab" htmlFor="prompt-input">
          {label}
        </label>
        <input
          id="prompt-input"
          className="field"
          value={value}
          autoFocus
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
        />
      </form>
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}
