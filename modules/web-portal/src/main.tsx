import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.js";
import "./style.css";
import { configureTelemetry } from "./telemetry.js";

void configureTelemetry(import.meta.env.VITE_APP_VERSION || "dev").finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode><App /></StrictMode>,
  );
});
