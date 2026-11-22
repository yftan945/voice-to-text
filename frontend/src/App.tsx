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
import { HTML_LANG, setLang, useLang, useT, type Lang, type StrKey } from "./i18n";
import { THEME_LABEL, useTheme, type ThemeChoice } from "./theme";

/** 導覽列的頁面：[路徑, 名稱, 窄螢幕的簡稱]（名稱是 i18n 字典的 key） */
const NAV: [string, StrKey, StrKey][] = [
  ["/", "nav.record", "nav.recordShort"],
  ["/tasks", "nav.tasks", "nav.tasksShort"],
  ["/dictionaries", "nav.dictionaries", "nav.dictionariesShort"],
  ["/voiceprints", "nav.voiceprints", "nav.voiceprintsShort"],
];

/** 分頁標題換成「頁名・語音轉文字」（結果頁由 JobPage 用檔名設定）；換語言時跟著換。 */
function useRouteTitle(pathname: string) {
  const t = useT();
  useEffect(() => {
    if (pathname.startsWith("/jobs/")) return;
    const key = NAV.find(([to]) => to === pathname)?.[1] ?? "nav.notFound";
    document.title = t("app.title", { page: t(key) });
  }, [pathname, t]);
}

/**
 * 站內換頁後焦點移到新頁面的 h1（讀屏軟體會念出新頁標題，Tab 從頁面內容開始）。
 * h1 還沒出現（例如結果頁還在載入）就等它出現；等待期間使用者已經把焦點移到別處就不搶。
 */
function useRouteFocus(pathname: string) {
  const first = useRef(true);
  useEffect(() => {
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
  const t = useT();
  const [theme, setTheme] = useTheme();
  const label = t("theme.label", { value: t(THEME_LABEL[theme]) });
  return (
    <MenuButton
      className={className}
      aria-label={label}
      title={label}
      items={() => [
        { header: t("theme.header") },
        ...(["system", "light", "dark"] as ThemeChoice[]).map((c) => ({
          label: t(THEME_LABEL[c]),
          checked: theme === c,
          onSelect: () => setTheme(c),
        })),
      ]}
    >
      <Icon name="contrast" />
    </MenuButton>
  );
}

/**
 * 語言切換：只有兩種語言，按一下直接換；按鈕上顯示要切換過去的語言（中文介面「EN」、英文介面「中」），
 * 說明（aria-label、title）也用那個語言寫，看不懂目前語言的人也找得到。
 */
function LangButton() {
  const t = useT();
  const lang = useLang();
  const next: Lang = lang === "en" ? "zh-TW" : "en";
  const label = t("lang.switchLabel");
  return (
    <button
      type="button"
      className="icon-btn lang-btn"
      lang={HTML_LANG[next]}
      aria-label={label}
      title={label}
      onClick={() => setLang(next)}
    >
      <span aria-hidden="true">{t("lang.switchTo")}</span>
    </button>
  );
}

/** 標題列：品牌、各頁連結（「任務」旁顯示進行中的數量）、外觀選單、語言切換。 */
function NavBar() {
  const t = useT();
  const { pathname } = useLocation();
  const onResult = pathname.startsWith("/jobs/");
  const activeCount = useActiveCount();
  return (
    <header className="nav">
      {/* 窄螢幕放不下完整品牌名稱時只顯示「語音」，再窄就收起（「錄音」連結也回首頁）；見 base.css */}
      <Link to="/" className="brand" aria-label={t("app.name")}>
        <span className="brand-full">{t("app.name")}</span>
        <span className="brand-short">{t("app.nameShort")}</span>
      </Link>
      {NAV.map(([to, key, shortKey]) => {
        const label = t(key);
        const short = t(shortKey);
        const badge = to === "/tasks" && activeCount > 0;
        return (
          // 用 Link 自己決定 aria-current：NavLink 會用它自己的比對結果蓋掉這裡給的值（結果頁就不會標「錄音」）
          <Link
            key={to}
            to={to}
            className="link"
            // 結果頁屬於「錄音」
            aria-current={(to === "/" ? pathname === "/" || onResult : pathname.startsWith(to)) ? "page" : undefined}
            // 窄螢幕顯示簡稱（英文名稱較長），讀屏軟體一律念完整名稱
            aria-label={badge ? t("nav.activeBadge", { label, n: activeCount }) : short !== label ? label : undefined}
          >
            {short === label ? (
              label
            ) : (
              <>
                <span className="l-full">{label}</span>
                <span className="l-short" aria-hidden="true">
                  {short}
                </span>
              </>
            )}
            {badge && (
              <span className="nav-badge num" aria-hidden="true">
                {activeCount > 99 ? "99+" : activeCount}
              </span>
            )}
          </Link>
        );
      })}
      <div className="nav-tools">
        <ThemeButton className="icon-btn theme-btn" />
        <LangButton />
      </div>
    </header>
  );
}

export default function App() {
  const t = useT();
  const { pathname } = useLocation();
  useRouteTitle(pathname);
  useRouteFocus(pathname);
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
                <p>{t("app.notFound")}</p>
              </main>
            }
          />
        </Routes>
      </JobsProvider>
    </ToastProvider>
  );
}
