import { useDashboardStore } from "../store/dashboard-store";

const labels = {
  connected: "MQTT connected",
  connecting: "Connecting MQTT",
  offline: "MQTT offline",
} as const;

export function StatusPill(): React.JSX.Element {
  const status = useDashboardStore((state) => state.connectionStatus);
  return <span className={`status-pill ${status}`}><i />{labels[status]}</span>;
}
