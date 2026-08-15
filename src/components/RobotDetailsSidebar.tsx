import { useEffect } from "react";
import type { ImuTelemetry } from "../domain/telemetry";
import { getRobotImu, useDashboardStore } from "../store/dashboard-store";

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

export function RobotDetailsSidebar(): React.JSX.Element | null {
  const selectedRobotId = useDashboardStore((state) => state.selectedRobotId);
  const robot = useDashboardStore((state) => selectedRobotId ? state.robots[selectedRobotId] : undefined);
  const closeSidebar = useDashboardStore((state) => state.closeSidebar);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeSidebar();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeSidebar]);

  if (!selectedRobotId || !robot) return null;
  const imu = getRobotImu(robot);
  const motors = robot.telemetry?.motor_telemetry;
  const motorStatus = motors === "Stopped"
    ? "Stopped"
    : motors?.Motoring ? `L ${format(motors.Motoring.left)} · R ${format(motors.Motoring.right)}` : "—";

  return (
    <aside className="robot-sidebar" aria-label={`Robot ${selectedRobotId} details`}>
      <header className="sidebar-header">
        <div><span className="eyebrow">Robot details</span><h2>{selectedRobotId}</h2></div>
        <button type="button" className="icon-button" onClick={closeSidebar} aria-label="Close details">×</button>
      </header>
      <div className="sidebar-scroll">
        <section className="detail-section">
          <h3>Position</h3>
          <InfoRow label="X" value={robot.pose?.x_m} unit="m" />
          <InfoRow label="Y" value={robot.pose?.y_m} unit="m" />
          <InfoRow label="Heading" value={robot.pose ? robot.pose.heading_rad * 180 / Math.PI : null} unit="°" />
          <InfoRow label="Speed" value={robot.pose?.speed_m_s} unit="m/s" />
          <InfoRow label="Variance" value={robot.pose?.position_variance_m2} unit="m²" />
          <InfoRow label="Anchors used" value={robot.pose?.anchors_used} />
          <InfoRow label="Timestamp" value={robot.pose?.timestamp_us} unit="µs" />
        </section>
        <section className="detail-section">
          <h3>Status</h3>
          <InfoRow label="Motors" value={motorStatus} />
          <InfoRow label="Battery" value={robot.telemetry?.battery_telemetry.state_of_charge} unit="%" />
          <InfoRow label="Pack voltage" value={robot.telemetry?.battery_telemetry.voltage} unit="V" />
          <InfoRow label="Current" value={robot.telemetry?.battery_telemetry.current} unit="A" />
          <InfoRow label="Charger temperature" value={robot.telemetry?.battery_telemetry.temperature} unit="°C" />
          <InfoRow label="Charging" value={robot.telemetry?.battery_telemetry.is_charging} />
          <InfoRow label="RSSI" value={robot.telemetry?.network_telemetry.rssi} unit="dBm" />
          <InfoRow label="IP" value={robot.telemetry?.network_telemetry.ip_address} />
        </section>
        {imu ? <ImuSection imu={imu} /> : <section className="detail-section"><h3>IMU</h3><p className="muted">Waiting for the first IMU sample.</p></section>}
      </div>
    </aside>
  );
}
