import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

/** 提示後面附的站內連結（例如「查看任務」）。 */
export interface ToastAction {
  label: string;
  to: string;
}

type ToastFn = (msg: string, action?: ToastAction) => void;

const ToastContext = createContext<ToastFn>(() => {});

/** 停留時間：附連結的提示多留一會兒，來得及點；滑鼠移上去或焦點在裡面時不消失。 */
const SHOW_MS = 3200;
const SHOW_ACTION_MS = 6000;
/** 淡出動畫（base.css .toast 的 transition）結束後再清掉內容 */
const FADE_MS = 200;

/**
 * 畫面底部的短暫提示（role=status，讀屏軟體會念出來）；可附一個站內連結。
 * 收起淡出後清掉內容：讀屏軟體瀏覽不到舊訊息、Tab 也走不到看不見的連結。
 * （不用 visibility:hidden／aria-hidden 藏整個區塊：live region 在顯示的同時換內容，有些讀屏軟體會漏念）
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState("");
  const [action, setAction] = useState<ToastAction | null>(null);
  const [show, setShow] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number>(0);
  const clearTimer = useRef<number>(0);
  const holdMs = useRef(SHOW_MS);

  const hide = useCallback(() => {
    setShow(false);
    window.clearTimeout(clearTimer.current);
    clearTimer.current = window.setTimeout(() => {
      setMsg("");
      setAction(null);
    }, FADE_MS);
  }, []);
  const startTimer = useCallback(() => {
    window.clearTimeout(timer.current);
    // 焦點在提示裡（例如正要按「查看任務」）時不計時，離開後再開始
    if (boxRef.current?.contains(document.activeElement)) return;
    timer.current = window.setTimeout(hide, holdMs.current);
  }, [hide]);
  const toast = useCallback<ToastFn>(
    (m, a) => {
      window.clearTimeout(clearTimer.current);
      setMsg(m);
      setAction(a ?? null);
      setShow(true);
      holdMs.current = a ? SHOW_ACTION_MS : SHOW_MS;
      startTimer();
    },
    [startTimer],
  );
  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
      window.clearTimeout(clearTimer.current);
    },
    [],
  );
  const hold = () => window.clearTimeout(timer.current);
  const release = () => {
    if (show) startTimer();
  };
  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div
        ref={boxRef}
        className={`toast${show ? " show" : ""}`}
        role="status"
        aria-live="polite"
        onMouseEnter={hold}
        onMouseLeave={release}
        onFocus={hold}
        onBlur={release}
      >
        {msg}
        {action && (
          <Link
            className="toast-act"
            to={action.to}
            tabIndex={show ? undefined : -1}
            onClick={(e) => {
              // 換頁後焦點不要留在即將消失的提示裡
              e.currentTarget.blur();
              window.clearTimeout(timer.current);
              hide();
            }}
          >
            {action.label}
          </Link>
        )}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
