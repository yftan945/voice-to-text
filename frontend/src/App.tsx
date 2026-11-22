import { useEffect, useRef } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import Icon from "./components/Icon";
import { MenuButton } from "./components/Menu";
import { ToastProvider } from "./components/Toast";
import DictionariesPage from "./pages/DictionariesPage";
import { JobsProvider, useActiveCount } from "./jobs";
import HomePage from "./pages/HomePage";
import JobPage from "./pages/JobPage";
import TasksPage from "./pages/TasksPage";
import VoiceprintsPage from "./pages/VoiceprintsPage";
import { THEME_LABEL, useTheme, type ThemeChoice } from "./theme";

const NAV: [string, string][] = [
  ["/", "錄音"],
  ["/tasks", "任務"],
  ["/dictionaries", "字典"],
  ["/voiceprints", "聲紋庫"],
];

const SITE = "語音轉文字";

/**
 * 換頁時：分頁標題換成「頁名・語音轉文字」（結果頁由 JobPage 用檔名設定），
 * 站內換頁後焦點移到新頁面的 h1（讀屏軟體會念出新頁標題，Tab 從頁面內容開始）。
 * h1 還沒出現（例如結果頁還在載入）就等它出現；等待期間使用者已經把焦點移到別處就不搶。
 */
function useRouteTitleAndFocus(pathname: string) {
  const first = useRef(true);
  useEffect(() => {
    if (!pathname.startsWith("/jobs/")) {
      const label = NAV.find(([to]) => to === pathname)?.[1] ?? "找不到頁面";
      document.title = `${label}・${SITE}`;
    }
    if (first.current) {
      // 第一次載入不移焦點（瀏覽器本來就從頁首開始）
      first.current = false;
      return;
    }
    const from = document.activeElement;
    const start = performance.now();
    let raf = 0;
    const tryFocus = () => {
      const h1 = document.querySelector<HTMLElement>("main h1");
      if (h1) {
        const now = document.activeElement;
        if (now && now !== document.body && now !== from) return;
        if (!h1.hasAttribute("tabindex")) h1.tabIndex = -1;
        h1.focus({ preventScroll: true });
        return;
      }
      if (performance.now() - start < 5000) raf = requestAnimationFrame(tryFocus);
    };
    tryFocus();
    return () => cancelAnimationFrame(raf);
  }, [pathname]);
}

/** 外觀選單：跟隨系統／淺色／深色。 */
export function ThemeButton({ className = "icon-btn" }: { className?: string }) {
  const [theme, setTheme] = useTheme();
  return (
    <MenuButton
      className={className}
      aria-label={`外觀：${THEME_LABEL[theme]}`}
      title={`外觀：${THEME_LABEL[theme]}`}
      items={() => [
        { header: "外觀" },
        ...(["system", "light", "dark"] as ThemeChoice[]).map((c) => ({
          label: THEME_LABEL[c],
          checked: theme === c,
          onSelect: () => setTheme(c),
        })),
      ]}
    >
      <Icon name="contrast" />
    </MenuButton>
  );
}

/** 標題列：品牌、各頁連結（「任務」旁顯示進行中的數量）、外觀選單。 */
function NavBar() {
  const { pathname } = useLocation();
  const onResult = pathname.startsWith("/jobs/");
  const activeCount = useActiveCount();
  return (
    <header className="nav">
      {/* 窄螢幕放不下完整品牌名稱時只顯示「語音」（見 base.css） */}
      <Link to="/" className="brand" aria-label="語音轉文字">
        <span className="brand-full">語音轉文字</span>
        <span className="brand-short">語音</span>
      </Link>
      {NAV.map(([to, label]) => {
        const badge = to === "/tasks" && activeCount > 0;
        return (
          // 用 Link 自己決定 aria-current：NavLink 會用它自己的比對結果蓋掉這裡給的值（結果頁就不會標「錄音」）
          <Link
            key={to}
            to={to}
            className="link"
            // 結果頁屬於「錄音」
            aria-current={(to === "/" ? pathname === "/" || onResult : pathname.startsWith(to)) ? "page" : undefined}
            aria-label={badge ? `${label}，${activeCount} 筆進行中` : undefined}
          >
            {label}
            {badge && (
              <span className="nav-badge num" aria-hidden="true">
                {activeCount > 99 ? "99+" : activeCount}
              </span>
            )}
          </Link>
        );
      })}
      <ThemeButton className="icon-btn theme-btn" />
    </header>
  );
}

export default function App() {
  const { pathname } = useLocation();
  useRouteTitleAndFocus(pathname);
  return (
    <ToastProvider>
      <JobsProvider>
        <NavBar />
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/history" element={<Navigate to="/" replace />} />
          <Route path="/jobs/:id" element={<JobPage />} />
          <Route path="/dictionaries" element={<DictionariesPage />} />
          <Route path="/voiceprints" element={<VoiceprintsPage />} />
          <Route
            path="*"
            element={
              <main className="page">
                <p>找不到這個頁面。</p>
              </main>
            }
          />
        </Routes>
      </JobsProvider>
    </ToastProvider>
  );
}
