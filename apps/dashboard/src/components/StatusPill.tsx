import { useLocale } from "../services/locale-context";
import { useDashboardStore } from "../store/dashboard-store";

export function StatusPill(): React.JSX.Element {
  const status = useDashboardStore((state) => state.connectionStatus);
  const { t } = useLocale();
  return <span className={`status-pill ${status}`}><i />{t.status[status]}</span>;
}
