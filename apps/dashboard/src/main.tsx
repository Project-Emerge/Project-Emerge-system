import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { GatewayProvider } from "./services/gateway-context";
import { ThemeProvider } from "./services/theme-context";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <GatewayProvider><App /></GatewayProvider>
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
);
