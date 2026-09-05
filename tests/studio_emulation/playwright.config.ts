// Стенд геометрии: браузер открывает СОБРАННОЕ дерево, а не исходники, — так меряется то же,
// что увидит человек. Сервер поднимается самим прогоном и им же гасится.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "geometry.spec.ts",
  use: { baseURL: "http://localhost:4173", viewport: { width: 1280, height: 800 } },
  webServer: {
    command: "npm run build && npx vite preview --port 4173 --strictPort",
    url: "http://localhost:4173",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
