// Сборка стенда: нужна только затем, чтобы браузер мог открыть дерево и померить геометрию.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({ plugins: [react()], build: { outDir: "dist" } });
