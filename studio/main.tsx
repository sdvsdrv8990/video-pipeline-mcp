import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/screens/App";
import "./app/tokens.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
