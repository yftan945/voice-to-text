import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/home.css";
import "./styles/result.css";
import "./styles/library.css";
import App from "./App";

// 字型宣告另外一個 CSS，非同步載入（見 fonts.ts）
void import("./fonts");

// data router：未儲存時要用 useBlocker 擋下站內換頁（BrowserRouter 不支援）
const router = createBrowserRouter([{ path: "*", element: <App /> }]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
