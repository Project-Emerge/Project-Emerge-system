import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { StatusPill } from "./components/StatusPill";
import { ThemeSwitcher } from "./components/ThemeSwitcher";
import { DashboardPage } from "./pages/DashboardPage";
import { ConfigurationPage } from "./pages/ConfigurationPage";

export function App(): React.JSX.Element {
  return (
    <div className="app-shell">
      <header className="app-header">
        <NavLink to="/" className="brand" aria-label="Project Emerge dashboard">
          <span className="brand-mark"><i /><i /><i /></span>
          <span><b>PROJECT</b> EMERGE<small>FLEET VIEW</small></span>
        </NavLink>
        <nav aria-label="Main navigation">
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/config">Settings</NavLink>
        </nav>
        <div className="header-actions">
          <ThemeSwitcher />
          <StatusPill />
        </div>
      </header>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/config" element={<ConfigurationPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
