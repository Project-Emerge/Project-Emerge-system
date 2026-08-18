import { useEffect, useState } from "react";
import type { ImuTelemetry } from "../domain/telemetry";
import { ManualDriveControl } from "./ManualDriveControl";
import {
  getRobotImu,
  isRobotStale,
  type RobotLiveState,
  useDashboardStore,
} from "../store/dashboard-store";

function format(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "—" : Number(value).toFixed(digits);
}

function InfoRow({ label, value, unit }: { label: string; value: string | number | boolean | null | undefined; unit?: string }): React.JSX.Element {
  const rendered = typeof value === "number" ? format(value) : value === true ? "Yes" : value === false ? "No" : value ?? "—";
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
  return (
    <>
      <section className="detail-section">
        <h3>IMU · raw sample</h3>
        <InfoRow label="Timestamp" value={imu.timestamp_us} unit="µs" />
        <VectorRows label="Accelerometer" vector={imu.raw.accelerometer} unit="m/s²" />
        <VectorRows label="Gyroscope" vector={imu.raw.gyroscope} unit="°/s" />
        <VectorRows label="Magnetometer" vector={imu.raw.magnetometer} unit="µT" />
        <InfoRow label="Sensor temperature" value={imu.raw.temperature} unit="°C" />
      </section>
      <section className="detail-section">
        <h3>IMU · filtered</h3>
        <VectorRows label="Accelerometer" vector={imu.filtered.accelerometer} unit="m/s²" />
        <VectorRows label="Gyroscope" vector={imu.filtered.gyroscope} unit="°/s" />
        <VectorRows label="Magnetometer" vector={imu.filtered.magnetometer} unit="µT" />
        <VectorRows label="Linear acceleration" vector={imu.filtered.linear_acceleration} unit="m/s²" />
        <div className="vector-group">
          <span className="vector-label">Quaternion</span>
          <div className="vector-values quad-values">
            {imu.filtered.quaternion.map((value, index) => <span key={index}>{["x", "y", "z", "w"][index]} {format(value, 3)}</span>)}
          </div>
        </div>
        <InfoRow label="Roll" value={imu.filtered.roll} unit="°" />
        <InfoRow label="Pitch" value={imu.filtered.pitch} unit="°" />
        <InfoRow label="Magnetic heading" value={imu.filtered.heading} unit="°" />
        <InfoRow label="Stationary" value={imu.filtered.is_stationary} />
      </section>
    </>
  );
}

function lastUpdateLabel(lastSeenAt: number, now: number): string {
  if (lastSeenAt <= 0) return "No live signal";
  const elapsedSeconds = Math.max(0, Math.floor((now - lastSeenAt) / 1_000));
  return elapsedSeconds < 1 ? "Just now" : `${elapsedSeconds}s ago`;
}

function RobotTelemetry({ robot, now }: { robot: RobotLiveState; now: number }): React.JSX.Element {
  const imu = getRobotImu(robot);
  const motors = robot.telemetry?.motor_telemetry;
  const motorStatus = motors === "Stopped"
    ? "Stopped"
    : motors?.Motoring ? `L ${format(motors.Motoring.left)} · R ${format(motors.Motoring.right)}` : "—";

  return (
    <div className="robot-telemetry">
      <section className="detail-section">
        <h3>Live status</h3>
        <InfoRow label="Last update" value={lastUpdateLabel(robot.lastSeenAt, now)} />
        <InfoRow label="Motors" value={motorStatus} />
        <InfoRow label="Battery" value={robot.telemetry?.battery_telemetry.state_of_charge} unit="%" />
        <InfoRow label="Pack voltage" value={robot.telemetry?.battery_telemetry.voltage} unit="V" />
        <InfoRow label="Current" value={robot.telemetry?.battery_telemetry.current} unit="A" />
        <InfoRow label="Charger temperature" value={robot.telemetry?.battery_telemetry.temperature} unit="°C" />
        <InfoRow label="Charging" value={robot.telemetry?.battery_telemetry.is_charging} />
        <InfoRow label="RSSI" value={robot.telemetry?.network_telemetry.rssi} unit="dBm" />
        <InfoRow label="IP" value={robot.telemetry?.network_telemetry.ip_address} />
      </section>
      <section className="detail-section">
        <h3>Position</h3>
        <InfoRow label="X" value={robot.pose?.x_m} unit="m" />
        <InfoRow label="Y" value={robot.pose?.y_m} unit="m" />
        <InfoRow label="Heading" value={robot.pose ? robot.pose.heading_rad * 180 / Math.PI : null} unit="°" />
        <InfoRow label="Speed" value={robot.pose?.speed_m_s} unit="m/s" />
        <InfoRow label="Variance" value={robot.pose?.position_variance_m2} unit="m²" />
        <InfoRow label="Timestamp" value={robot.pose?.timestamp_us} unit="µs" />
      </section>
      {imu
        ? <ImuSection imu={imu} />
        : <section className="detail-section"><h3>IMU</h3><p className="muted">Waiting for the first IMU sample.</p></section>}
    </div>
  );
}

function RobotCard({ robot, expanded, now }: { robot: RobotLiveState; expanded: boolean; now: number }): React.JSX.Element {
  const toggleSelectedRobot = useDashboardStore((state) => state.toggleSelectedRobot);
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
        <span className="robot-avatar" aria-hidden="true"><i /></span>
        <span className="robot-card-copy">
          <strong>{robot.id}</strong>
          <span className={stale ? "robot-reachability stale" : "robot-reachability"}>
            <i />{stale ? "Unavailable" : "Reachable"}
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
    <aside className="robot-sidebar panel" aria-label="Reachable robots">
      <header className="sidebar-header">
        <div><span className="eyebrow">Live fleet</span><h2>Robots</h2></div>
        <span className="reachable-count"><i />{reachableCount} reachable</span>
      </header>
      <div className="sidebar-scroll">
        {robotIds.length === 0
          ? <p className="empty-message">Reachable robots appear after their first live message.</p>
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
