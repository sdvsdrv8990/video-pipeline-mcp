import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Ключей здесь нет и не будет: условие отбора владельца (20 §4а) — ни одной зависимости с
// API-ключом, потому что ключ означает внешнего получателя наших данных.
export default defineConfig({
  plugins: [react(), tailwind()],
  server: { port: 3000 },
});
