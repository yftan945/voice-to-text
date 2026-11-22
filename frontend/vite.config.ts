import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

/**
 * @fontsource 的 CSS 每個字型切片都同時列 woff2 與 woff；支援 woff2 的瀏覽器不會用到 woff，
 * 把 woff 那一段拿掉，建置結果就不會多帶一份（約少 13 MB）。
 */
function woff2Only(): Plugin {
  return {
    name: "fontsource-woff2-only",
    enforce: "pre",
    transform(code, id) {
      if (!/@fontsource[\\/].+\.css$/.test(id)) return null;
      return { code: code.replace(/,\s*url\([^)]+\.woff\)\s*format\(['"]woff['"]\)/g, ""), map: null };
    },
  };
}

// 開發時把 /api 轉到本機或 GPU 主機上的後端：VITE_API=http://<GPU 主機 IP>:8810 npm run dev
export default defineConfig({
  plugins: [woff2Only(), react()],
  server: {
    proxy: {
      "/api": { target: process.env.VITE_API ?? "http://127.0.0.1:8810", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
