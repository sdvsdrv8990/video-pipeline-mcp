import { tokens } from "./tokens";

// Кадры объявлены ОДИН раз и здесь: анимация примитива — пресет, а не свойство, размазанное
// по компонентам (иначе её нечем вернуть, когда ИИ её сотрёт).
export const appear = `@keyframes appear {
  from { opacity: 0; transform: translateY(${tokens.space.sm}); }
  to { opacity: 1; transform: none; }
}`;
