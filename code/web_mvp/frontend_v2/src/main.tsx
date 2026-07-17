import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { DesktopIpcBootProbe } from "./desktop/DesktopIpcBootProbe";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DesktopIpcBootProbe />
    <App />
  </StrictMode>,
);
