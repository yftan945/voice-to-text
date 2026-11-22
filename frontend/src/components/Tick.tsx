import type { ReactNode } from "react";

/** 帶方框的文字切換（取代 iOS 風格開關），aria-pressed 表示開或關。 */
export default function Tick({
  on,
  onChange,
  children,
  disabled,
  className = "",
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={`tick ${className}`}
      aria-pressed={on}
      disabled={disabled}
      onClick={() => onChange(!on)}
    >
      <span className="box" aria-hidden="true">
        <svg viewBox="0 0 12 12">
          <path d="M2 6.3l2.6 2.5L10 3.5" />
        </svg>
      </span>
      {children}
    </button>
  );
}
