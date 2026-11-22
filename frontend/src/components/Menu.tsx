import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export type MenuItem =
  | { header: string }
  | { sep: true }
  | {
      label: ReactNode;
      onSelect?: () => void;
      /** 連結：預設是下載（匯出用）；external＝在新分頁開啟 */
      href?: string;
      external?: boolean;
      icon?: ReactNode;
      checked?: boolean;
      danger?: boolean;
      disabled?: boolean;
    };

/**
 * 錨點彈出選單：開啟時焦點放在第一項（或打勾的那項），↑↓ 移動、Esc 關閉並把焦點還給錨點，
 * 點選單外面、Tab 離開時關閉。
 */
export default function Menu({
  anchor,
  items,
  onClose,
  align = "right",
  label,
}: {
  anchor: HTMLElement;
  items: MenuItem[];
  onClose: (refocus: boolean) => void;
  align?: "left" | "right";
  label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    const r = anchor.getBoundingClientRect();
    const mw = m.offsetWidth;
    const mh = m.offsetHeight;
    let left = align === "left" ? r.left : r.right - mw;
    left = Math.min(Math.max(8, left), window.innerWidth - mw - 8);
    let top = r.bottom + 6;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
    m.style.left = `${left}px`;
    m.style.top = `${top}px`;
    const btns = Array.from(m.querySelectorAll<HTMLElement>(".mi:not([aria-disabled='true'])"));
    (btns.find((b) => b.getAttribute("aria-checked") === "true") ?? btns[0])?.focus({ preventScroll: true });
    anchor.setAttribute("aria-expanded", "true");
    return () => anchor.removeAttribute("aria-expanded");
  }, [anchor, align]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor.contains(t)) closeRef.current(false);
    };
    const onResize = () => closeRef.current(false);
    document.addEventListener("pointerdown", onDown, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("resize", onResize);
    };
  }, [anchor]);

  const onKey = (e: React.KeyboardEvent) => {
    const btns = Array.from(ref.current?.querySelectorAll<HTMLElement>(".mi:not([aria-disabled='true'])") ?? []);
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeRef.current(true);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const i = btns.indexOf(document.activeElement as HTMLElement);
      btns[(i + (e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length]?.focus();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      btns[e.key === "Home" ? 0 : btns.length - 1]?.focus();
    } else if (e.key === "Tab") {
      closeRef.current(false);
    }
  };

  return createPortal(
    <div className="menu" role="menu" aria-label={label} ref={ref} onKeyDown={onKey}>
      {items.map((it, i) => {
        if ("sep" in it) return <hr key={i} />;
        if ("header" in it)
          return (
            <div key={i} className="mh" role="presentation">
              {it.header}
            </div>
          );
        const role = it.checked === undefined ? "menuitem" : "menuitemcheckbox";
        const cls = `mi${it.danger ? " danger" : ""}`;
        const body = (
          <>
            {it.icon}
            <span>{it.label}</span>
            {it.checked !== undefined && (
              <svg className="chk" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M3 8.5l3.2 3L13 4.5" />
              </svg>
            )}
          </>
        );
        if (it.href)
          return (
            <a
              key={i}
              className={cls}
              role={role}
              href={it.href}
              {...(it.external ? { target: "_blank", rel: "noopener noreferrer" } : { download: true })}
              onClick={() => closeRef.current(false)}
            >
              {body}
            </a>
          );
        return (
          <button
            key={i}
            type="button"
            className={cls}
            role={role}
            aria-checked={it.checked}
            aria-disabled={it.disabled || undefined}
            onClick={() => {
              if (it.disabled) return;
              // 先把焦點還給錨點再執行：接著打開的對話框關閉後，焦點才回得到原來的位置
              closeRef.current(true);
              it.onSelect?.();
            }}
          >
            {body}
          </button>
        );
      })}
    </div>,
    document.body,
  );
}

/** 按鈕＋它的選單（items 每次打開時才算，內容跟著最新狀態）。 */
export function MenuButton({
  className = "btn",
  items,
  align,
  children,
  ...rest
}: {
  className?: string;
  items: () => MenuItem[];
  align?: "left" | "right";
  children: ReactNode;
  "aria-label"?: string;
  title?: string;
  disabled?: boolean;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  return (
    <>
      <button
        type="button"
        className={className}
        aria-haspopup="menu"
        onClick={(e) => {
          const el = e.currentTarget;
          setAnchor((a) => (a ? null : el));
        }}
        {...rest}
      >
        {children}
      </button>
      {anchor && (
        <Menu
          anchor={anchor}
          items={items()}
          align={align}
          label={rest["aria-label"]}
          onClose={(refocus) => {
            setAnchor(null);
            if (refocus) anchor.focus({ preventScroll: true });
          }}
        />
      )}
    </>
  );
}
