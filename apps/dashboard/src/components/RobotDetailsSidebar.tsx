import { useEffect, useState } from "react";
import { bodyHeadingRad, type ImuTelemetry } from "../domain/telemetry";
import { ManualDriveControl } from "./ManualDriveControl";
import { useLocale } from "../services/locale-context";
import {
  getRobotImu,
  isRobotStale,
  type RobotLiveState,
  useDashboardStore,
} from "../store/dashboard-store";

function format(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "—" : Number(value).toFixed(digits);
}

function InfoRow({
  label,
  value,
  unit,
  yesLabel = "Yes",
  noLabel = "No",
}: {
  label: string;
  value: string | number | boolean | null | undefined;
  unit?: string;
  yesLabel?: string;
  noLabel?: string;
}): React.JSX.Element {
  const rendered = typeof value === "number" ? format(value) : value === true ? yesLabel : value === false ? noLabel : value ?? "—";
  return <div className="info-row"><span>{label}</span><strong>{rendered}{unit ? ` ${unit}` : ""}</strong></div>;
}

function VectorRows({ label, vector, unit }: { label: string; vector: [number, number, number]; unit: string }): React.JSX.Element {
  return (
    <div className="vector-group">
      <span className="vector-label">{label}</span>
      <div className="vector-values">
        <span>x {format(vector[0])} {unit}</span>
        <span>y {format(vector[1])} {unit}</span>
        <span>z {format(vector[2])} {unit}</span>
      </div>
    </div>
  );
}

function ImuSection({ imu }: { imu: ImuTelemetry }): React.JSX.Element {
  const { t } = useLocale();
  return (
    <>
      <section className="detail-section">
        <h3>{t.sidebar.imuRaw}</h3>
        <InfoRow label={t.sidebar.timestamp} value={imu.timestamp_us} unit="µs" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <VectorRows label={t.sidebar.accelerometer} vector={imu.raw.accelerometer} unit="m/s²" />
        <VectorRows label={t.sidebar.gyroscope} vector={imu.raw.gyroscope} unit="°/s" />
        <VectorRows label={t.sidebar.magnetometer} vector={imu.raw.magnetometer} unit="µT" />
        <InfoRow label={t.sidebar.sensorTemp} value={imu.raw.temperature} unit="°C" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
      </section>
      <section className="detail-section">
        <h3>{t.sidebar.imuFiltered}</h3>
        <VectorRows label={t.sidebar.accelerometer} vector={imu.filtered.accelerometer} unit="m/s²" />
        <VectorRows label={t.sidebar.gyroscope} vector={imu.filtered.gyroscope} unit="°/s" />
        <VectorRows label={t.sidebar.magnetometer} vector={imu.filtered.magnetometer} unit="µT" />
        <VectorRows label={t.sidebar.linearAcceleration} vector={imu.filtered.linear_acceleration} unit="m/s²" />
        <div className="vector-group">
          <span className="vector-label">{t.sidebar.quaternion}</span>
          <div className="vector-values quad-values">
            {imu.filtered.quaternion.map((value, index) => <span key={index}>{["x", "y", "z", "w"][index]} {format(value, 3)}</span>)}
          </div>
        </div>
        <InfoRow label={t.sidebar.roll} value={imu.filtered.roll} unit="°" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.pitch} value={imu.filtered.pitch} unit="°" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.magneticHeading} value={imu.filtered.heading} unit="°" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.stationary} value={imu.filtered.is_stationary} yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
      </section>
    </>
  );
}

function lastUpdateLabel(lastSeenAt: number, now: number, t: ReturnType<typeof useLocale>["t"]): string {
  if (lastSeenAt <= 0) return t.sidebar.noLiveSignal;
  const elapsedSeconds = Math.max(0, Math.floor((now - lastSeenAt) / 1_000));
  return elapsedSeconds < 1 ? t.sidebar.justNow : t.sidebar.secondsAgo(elapsedSeconds);
}

function RobotTelemetry({ robot, now }: { robot: RobotLiveState; now: number }): React.JSX.Element {
  const { t } = useLocale();
  const imu = getRobotImu(robot);
  const motors = robot.telemetry?.motor_telemetry;
  const motorStatus = motors === "Stopped"
    ? t.sidebar.stopped
    : motors?.Motoring ? t.sidebar.motoring(format(motors.Motoring.left), format(motors.Motoring.right)) : "—";

  return (
    <div className="robot-telemetry">
      <section className="detail-section">
        <h3>{t.sidebar.liveStatus}</h3>
        <InfoRow label={t.sidebar.lastUpdate} value={lastUpdateLabel(robot.lastSeenAt, now, t)} yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.motors} value={motorStatus} yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.battery} value={robot.telemetry?.battery_telemetry.state_of_charge} unit="%" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.packVoltage} value={robot.telemetry?.battery_telemetry.voltage} unit="V" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.current} value={robot.telemetry?.battery_telemetry.current} unit="A" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.chargerTemp} value={robot.telemetry?.battery_telemetry.temperature} unit="°C" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.charging} value={robot.telemetry?.battery_telemetry.is_charging} yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.rssi} value={robot.telemetry?.network_telemetry.rssi} unit="dBm" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.ip} value={robot.telemetry?.network_telemetry.ip_address} yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
      </section>
      <section className="detail-section">
        <h3>{t.sidebar.position}</h3>
        <InfoRow label="X" value={robot.pose?.x_m} unit="m" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label="Y" value={robot.pose?.y_m} unit="m" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.heading} value={robot.pose ? bodyHeadingRad(robot.pose) * 180 / Math.PI : null} unit="°" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.speed} value={robot.pose?.speed_m_s} unit="m/s" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.variance} value={robot.pose?.position_variance_m2} unit="m²" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
        <InfoRow label={t.sidebar.timestamp} value={robot.pose?.timestamp_us} unit="µs" yesLabel={t.sidebar.yes} noLabel={t.sidebar.no} />
      </section>
      {imu
        ? <ImuSection imu={imu} />
        : <section className="detail-section"><h3>{t.sidebar.imu}</h3><p className="muted">{t.sidebar.waitingImu}</p></section>}
    </div>
  );
}

function RobotCard({ robot, expanded, now }: { robot: RobotLiveState; expanded: boolean; now: number }): React.JSX.Element {
  const { t } = useLocale();
  const toggleSelectedRobot = useDashboardStore((state) => state.toggleSelectedRobot);
  const isLeader = useDashboardStore((state) => state.formation?.leaderId === robot.id);
  const stale = isRobotStale(robot, now);
  const battery = robot.telemetry?.battery_telemetry.state_of_charge;
  const detailsId = `robot-${robot.id}-telemetry`;

  return (
    <article className={`robot-card${expanded ? " expanded" : ""}`}>
      <button
        type="button"
        className="robot-card-trigger"
        aria-expanded={expanded}
        aria-controls={detailsId}
        onClick={() => toggleSelectedRobot(robot.id)}
      >
        <span className={isLeader ? "robot-avatar leader" : "robot-avatar"} aria-hidden="true"><i /></span>
        <span className="robot-card-copy">
          <span className="robot-card-name">
            <strong>{robot.id}</strong>
            {isLeader && <span className="robot-leader-badge">{t.sidebar.leaderBadge}</span>}
          </span>
          <span className={stale ? "robot-reachability stale" : "robot-reachability"}>
            <i />{stale ? t.sidebar.unavailable : t.sidebar.reachable}
          </span>
        </span>
        {battery !== undefined && <span className="robot-battery">{battery}%</span>}
        <span className="robot-chevron" aria-hidden="true">⌄</span>
      </button>
      {expanded && <div id={detailsId} className="robot-card-dropdown"><RobotTelemetry robot={robot} now={now} /></div>}
    </article>
  );
}

export function RobotDetailsSidebar(): React.JSX.Element {
  const { t } = useLocale();
  const robotIds = useDashboardStore((state) => state.robotIds);
  const robots = useDashboardStore((state) => state.robots);
  const selectedRobotId = useDashboardStore((state) => state.selectedRobotId);
  const closeSidebar = useDashboardStore((state) => state.closeSidebar);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeSidebar();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [closeSidebar]);

  const reachableCount = robotIds.reduce((count, id) => {
    const robot = robots[id];
    return count + (robot && !isRobotStale(robot, now) ? 1 : 0);
  }, 0);

  return (
    <aside className="robot-sidebar panel" aria-label={t.sidebar.ariaSidebar}>
      <header className="sidebar-header">
        <div><span className="eyebrow">{t.sidebar.liveFleet}</span><h2>{t.sidebar.robots}</h2></div>
        <span className="reachable-count"><i />{t.sidebar.reachableCount(reachableCount)}</span>
      </header>
      <div className="sidebar-scroll">
        {robotIds.length === 0
          ? <p className="empty-message">{t.sidebar.emptyMessage}</p>
          : <div className="robot-list">{robotIds.map((id) => {
            const robot = robots[id];
            return robot
              ? <RobotCard key={id} robot={robot} expanded={selectedRobotId === id} now={now} />
              : null;
          })}</div>}
      </div>
      <ManualDriveControl now={now} />
    </aside>
  );
}
