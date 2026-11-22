// 自己架設的字型（不連 Google Fonts）：@fontsource 已依 unicode-range 切成小檔，瀏覽器只下載畫面用到的字。
// 只載入用到的字重：宋體 400（逐字稿）、600（標題）；黑體 400（介面）、600（按鈕、講者名）。
// 四個字重的 @font-face 宣告約 500 KB（伺服器沒有 gzip），由 main.tsx 以動態 import 非同步載入，不擋第一次繪製；
// 載入前先用系統字（Songti TC／PingFang TC），載入後換上（font-display: swap）。
import "@fontsource/noto-serif-tc/400.css";
import "@fontsource/noto-serif-tc/600.css";
import "@fontsource/noto-sans-tc/400.css";
import "@fontsource/noto-sans-tc/600.css";
