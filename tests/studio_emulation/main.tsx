// Точка монтирования СТЕНДА, а не студии: судимое дерево живёт в `app/`, здесь только подъём.
import { createRoot } from "react-dom/client";

import { App } from "./app/screens/App";

const корень = document.getElementById("root");
if (корень) {
  createRoot(корень).render(<App />);
}
