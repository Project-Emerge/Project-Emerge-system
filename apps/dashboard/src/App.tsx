import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { StatusPill } from "./components/StatusPill";
import { ThemeSwitcher } from "./components/ThemeSwitcher";
import { DashboardPage } from "./pages/DashboardPage";
import { ConfigurationPage } from "./pages/ConfigurationPage";
import { useLocale } from "./services/locale-context";

export function App(): React.JSX.Element {
  const { t } = useLocale();

  return (
    <div className="app-shell">
      <header className="app-header">
        <NavLink to="/" className="brand" aria-label={t.nav.ariaBrand}>
          <span className="brand-mark"><i /><i /><i /></span>
          <span><b>PROJECT</b> EMERGE<small>{t.nav.brandSubtitle}</small></span>
        </NavLink>
        <nav aria-label={t.nav.ariaNav}>
          <NavLink to="/" end>{t.nav.dashboard}</NavLink>
          <NavLink to="/config">{t.nav.settings}</NavLink>
        </nav>
        <div className="header-actions">
          <LanguageSwitcher />
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
