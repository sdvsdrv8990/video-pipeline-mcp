// Настройка линтера дерева студии. Правила берутся у библиотек, своё здесь — только политика:
// хуки React судятся строго (правило хуков и полнота зависимостей), потому что три беды правки
// React-кода в этом проекте начинаются именно с эффекта и его зависимостей.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
    },
  },
);
