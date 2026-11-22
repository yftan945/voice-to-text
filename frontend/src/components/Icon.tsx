/** 線條圖示（24×24）。fill 類的圖示（播放、暫停、⋯）用實心。 */
const PATHS = {
  play: <path d="M8 5.5v13l11-6.5z" />,
  pause: <path d="M7 5h3.6v14H7zM13.4 5H17v14h-3.4z" />,
  more: (
    <>
      <circle cx="5.5" cy="12" r="1.7" />
      <circle cx="12" cy="12" r="1.7" />
      <circle cx="18.5" cy="12" r="1.7" />
    </>
  ),
  prev: <path d="M6 5v14M19 5l-9 7 9 7z" />,
  next: <path d="M18 5v14M5 5l9 7-9 7z" />,
  back15: (
    <>
      <path d="M4 12.5a8 8 0 1 0 2.4-5.7" />
      <path d="M4 4.5v4.2h4.2" />
      <text x="12.4" y="15.6" textAnchor="middle" fontSize="7.4" fontWeight="700" fill="currentColor" stroke="none">
        15
      </text>
    </>
  ),
  fwd15: (
    <>
      <path d="M20 12.5a8 8 0 1 1-2.4-5.7" />
      <path d="M20 4.5v4.2h-4.2" />
      <text x="11.6" y="15.6" textAnchor="middle" fontSize="7.4" fontWeight="700" fill="currentColor" stroke="none">
        15
      </text>
    </>
  ),
  chevL: <path d="M15 5l-7 7 7 7" />,
  chevD: <path d="M5 9l7 7 7-7" />,
  edit: (
    <>
      <path d="M4 20h4L19 9l-4-4L4 16v4z" />
      <path d="M13.5 6.5l4 4" />
    </>
  ),
  swap: (
    <>
      <path d="M7 7h11l-3-3" />
      <path d="M17 17H6l3 3" />
    </>
  ),
  copy: (
    <>
      <rect x="8" y="8" width="11.5" height="11.5" rx="2" />
      <path d="M16 8V6.5A2.5 2.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A2.5 2.5 0 0 0 6.5 16H8" />
    </>
  ),
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  upload: <path d="M12 16V4M7 9l5-5 5 5M5 20h14" />,
  grip: (
    <>
      <circle cx="9" cy="6" r="1.4" />
      <circle cx="15" cy="6" r="1.4" />
      <circle cx="9" cy="12" r="1.4" />
      <circle cx="15" cy="12" r="1.4" />
      <circle cx="9" cy="18" r="1.4" />
      <circle cx="15" cy="18" r="1.4" />
    </>
  ),
  contrast: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none" />
    </>
  ),
  expand: <path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" />,
  shrink: <path d="M20 10h-6V4M4 14h6v6M14 10l7-7M10 14l-7 7" />,
} as const;

const FILLED = new Set<IconName>(["play", "pause", "more", "grip"]);

export type IconName = keyof typeof PATHS;

export default function Icon({ name, size }: { name: IconName; size?: number }) {
  return (
    <svg
      className={`i${FILLED.has(name) ? " fill" : ""}`}
      viewBox="0 0 24 24"
      aria-hidden="true"
      style={size ? { width: size, height: size } : undefined}
    >
      {PATHS[name]}
    </svg>
  );
}
