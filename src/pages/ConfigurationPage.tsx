import { useEffect, useMemo, useState } from "react";
import {
  ANCHOR_IDS,
  AnchorsConfigurationSchema,
  EstimationConfigurationSchema,
  RobotConfigurationSchema,
  robotConfigurationTopic,
  type AnchorCalibration,
} from "../../shared/protocol";
import {
  DISTANCE_KEYS,
  solveAnchorCoordinates,
  type CalibrationSolution,
  type DistanceKey,
  type DistanceMeasurements,
} from "../domain/calibration";
import { useGatewayClient } from "../services/gateway-context";
import { useDashboardStore } from "../store/dashboard-store";

type AnchorDraft = AnchorCalibration;
type SaveState = { kind: "idle" | "saving" | "success" | "error"; message?: string };

const distanceLabels: Record<DistanceKey, string> = {
  a1a2: "A1 — A2",
  a2a3: "A2 — A3",
  a3a4: "A3 — A4",
  a4a1: "A4 — A1",
  a1a3: "A1 — A3 (diagonal)",
  a2a4: "A2 — A4 (diagonal)",
};

function defaultAnchors(): AnchorDraft[] {
  return ANCHOR_IDS.map((anchor_id, index) => ({
    anchor_id,
    x: index === 1 || index === 2 ? 6 : 0,
    y: index >= 2 ? 6 : 0,
    z: 2,
    offset_mm: 0,
    scale_ppm: 0,
  }));
}

function anchorLabel(anchorId: number): string {
  return `A${ANCHOR_IDS.indexOf(anchorId as (typeof ANCHOR_IDS)[number]) + 1}`;
}

function numberValue(event: React.ChangeEvent<HTMLInputElement>): number {
  return Number(event.target.value);
}

function StatusMessage({ state }: { state: SaveState }): React.JSX.Element | null {
  if (state.kind === "idle" || state.kind === "saving") return null;
  return <p className={`form-message ${state.kind}`}>{state.message}</p>;
}

export function ConfigurationPage(): React.JSX.Element {
  const gateway = useGatewayClient();
  const retainedAnchors = useDashboardStore((state) => state.anchorsConfiguration);
  const retainedEstimation = useDashboardStore((state) => state.estimationConfiguration);
  const robotIds = useDashboardStore((state) => state.robotIds);
  const selectedRobotId = useDashboardStore((state) => state.selectedRobotId);
  const [anchors, setAnchors] = useState<AnchorDraft[]>(defaultAnchors);
  const [antennaHeight, setAntennaHeight] = useState(0.06);
  const [measurements, setMeasurements] = useState<DistanceMeasurements>(() => Object.fromEntries(DISTANCE_KEYS.map((key) => [key, 0])) as DistanceMeasurements);
  const [solution, setSolution] = useState<CalibrationSolution | null>(null);
  const [calibrationError, setCalibrationError] = useState<string | null>(null);
  const [anchorSave, setAnchorSave] = useState<SaveState>({ kind: "idle" });
  const [fusionEnabled, setFusionEnabled] = useState(true);
  const [estimationSave, setEstimationSave] = useState<SaveState>({ kind: "idle" });
  const [activeRobotId, setActiveRobotId] = useState("");
  const robotConfiguration = useDashboardStore((state) => activeRobotId ? state.robots[activeRobotId]?.configuration : undefined);
  const [emaEnabled, setEmaEnabled] = useState(true);
  const [emaAlpha, setEmaAlpha] = useState(0.1);
  const [maxSpeed, setMaxSpeed] = useState(1);
  const [robotSave, setRobotSave] = useState<SaveState>({ kind: "idle" });

  useEffect(() => {
    if (!retainedAnchors) return;
    setAnchors(retainedAnchors.anchors);
    setAntennaHeight(retainedAnchors.robot_antenna_height_m);
  }, [retainedAnchors]);

  useEffect(() => {
    if (retainedEstimation) setFusionEnabled(retainedEstimation.fusion_enabled);
  }, [retainedEstimation]);

  useEffect(() => {
    if (!activeRobotId && robotIds.length) {
      setActiveRobotId(selectedRobotId && robotIds.includes(selectedRobotId) ? selectedRobotId : robotIds[0]);
    }
  }, [activeRobotId, robotIds, selectedRobotId]);

  useEffect(() => {
    if (!robotConfiguration) return;
    setEmaEnabled(robotConfiguration.motors.ema_filter_alpha !== null);
    setEmaAlpha(robotConfiguration.motors.ema_filter_alpha ?? 0.1);
    setMaxSpeed(robotConfiguration.motors.max_speed);
  }, [robotConfiguration]);

  const residualRows = useMemo(
    () => solution ? DISTANCE_KEYS.map((key) => ({ key, residual: solution.residuals[key] })) : [],
    [solution],
  );

  function updateAnchor(index: number, field: keyof AnchorDraft, value: number): void {
    setAnchors((current) => current.map((anchor, currentIndex) => currentIndex === index ? { ...anchor, [field]: value } : anchor));
  }

  function calculateCoordinates(): void {
    try {
      const nextSolution = solveAnchorCoordinates(measurements);
      setSolution(nextSolution);
      setCalibrationError(null);
      setAnchors((current) => current.map((anchor, index) => ({
        ...anchor,
        x: nextSolution.coordinates[index].x,
        y: nextSolution.coordinates[index].y,
      })));
    } catch (error) {
      setSolution(null);
      setCalibrationError(error instanceof Error ? error.message : "Unable to estimate coordinates.");
    }
  }

  async function saveAnchors(): Promise<void> {
    const payload = { robot_antenna_height_m: antennaHeight, anchors };
    const parsed = AnchorsConfigurationSchema.safeParse(payload);
    if (!parsed.success) {
      setAnchorSave({ kind: "error", message: "Check all coordinates and calibration values." });
      return;
    }
    setAnchorSave({ kind: "saving" });
    try {
      await gateway.publish("/config/anchors", parsed.data);
      setAnchorSave({ kind: "success", message: "Anchors saved." });
    } catch (error) {
      setAnchorSave({ kind: "error", message: error instanceof Error ? error.message : "Save failed." });
    }
  }

  async function saveRobotConfiguration(): Promise<void> {
    if (!activeRobotId) return;
    const payload = { motors: { ema_filter_alpha: emaEnabled ? emaAlpha : null, max_speed: maxSpeed } };
    const parsed = RobotConfigurationSchema.safeParse(payload);
    if (!parsed.success) {
      setRobotSave({ kind: "error", message: "Motor settings are not valid." });
      return;
    }
    setRobotSave({ kind: "saving" });
    try {
      await gateway.publish(robotConfigurationTopic(activeRobotId), parsed.data);
      setRobotSave({ kind: "success", message: "Settings saved." });
    } catch (error) {
      setRobotSave({ kind: "error", message: error instanceof Error ? error.message : "Save failed." });
    }
  }

  async function saveEstimationConfiguration(): Promise<void> {
    const parsed = EstimationConfigurationSchema.safeParse({ fusion_enabled: fusionEnabled });
    if (!parsed.success) {
      setEstimationSave({ kind: "error", message: "Estimation mode is not valid." });
      return;
    }
    setEstimationSave({ kind: "saving" });
    try {
      await gateway.publish("/config/estimation", parsed.data);
      setEstimationSave({ kind: "success", message: fusionEnabled ? "Sensor fusion enabled." : "Raw UWB mode enabled." });
    } catch (error) {
      setEstimationSave({ kind: "error", message: error instanceof Error ? error.message : "Save failed." });
    }
  }

  return (
    <main className="configuration-page">
      <section className="page-heading">
        <span className="eyebrow">Arena</span>
        <h1>Calibration and settings</h1>
        <p>Set the arena geometry and robot settings.</p>
      </section>
      <div className="configuration-grid">
        <section className="panel calibration-panel">
          <div className="panel-heading"><div><span className="eyebrow">1 · Measurements</span><h2>Horizontal distances</h2></div><span className="unit-tag">m</span></div>
          <p className="muted"><code>z</code> is set separately; this step estimates x/y in the arena plane.</p>
          <div className="measurement-grid">
            {DISTANCE_KEYS.map((key) => (
              <label key={key} className="field-label">
                {distanceLabels[key]}
                <input aria-label={distanceLabels[key]} type="number" min="0" step="0.001" value={measurements[key] || ""} onChange={(event) => setMeasurements((current) => ({ ...current, [key]: numberValue(event) }))} />
              </label>
            ))}
          </div>
          <button type="button" className="primary-button" onClick={calculateCoordinates}>Estimate coordinates</button>
          {calibrationError && <p className="form-message error">{calibrationError}</p>}
          {solution && (
            <div className="solver-result">
              <div><span>RMS error</span><strong>{(solution.rms * 1000).toFixed(1)} mm</strong></div>
              <div><span>Iterations</span><strong>{solution.iterations}</strong></div>
              <div className="residual-list">
                {residualRows.map(({ key, residual }) => <span key={key}>{distanceLabels[key]} <b>{(residual * 1000).toFixed(1)} mm</b></span>)}
              </div>
            </div>
          )}
        </section>

        <section className="panel anchors-panel">
          <div className="panel-heading"><div><span className="eyebrow">2 · Anchors</span><h2>Positions and calibration</h2></div></div>
          <label className="field-label antenna-field">Robot antenna height (m)<input aria-label="Robot antenna height" type="number" min="0" step="0.001" value={antennaHeight} onChange={(event) => setAntennaHeight(numberValue(event))} /></label>
          <div className="anchor-table-wrap">
            <table className="anchor-table">
              <thead><tr><th>Anchor</th><th>x m</th><th>y m</th><th>z m</th><th>Offset mm</th><th>Scale ppm</th></tr></thead>
              <tbody>
                {anchors.map((anchor, index) => (
                  <tr key={anchor.anchor_id}>
                    <th scope="row"><span>{anchorLabel(anchor.anchor_id)}</span><small>0x{anchor.anchor_id.toString(16).toUpperCase()}</small></th>
                    {(["x", "y", "z", "offset_mm", "scale_ppm"] as const).map((field) => <td key={field}><input aria-label={`${anchorLabel(anchor.anchor_id)} ${field}`} type="number" step={field === "x" || field === "y" || field === "z" ? "0.001" : "1"} value={anchor[field]} onChange={(event) => updateAnchor(index, field, numberValue(event))} /></td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="save-row"><button type="button" className="primary-button" disabled={anchorSave.kind === "saving"} onClick={saveAnchors}>{anchorSave.kind === "saving" ? "Saving…" : "Save anchors"}</button><StatusMessage state={anchorSave} /></div>
        </section>
      </div>

      <section className="panel estimation-panel">
        <div className="panel-heading"><div><span className="eyebrow">3 · Estimation</span><h2>Position mode</h2></div><span className={`mode-tag ${fusionEnabled ? "enabled" : "raw"}`}>{fusionEnabled ? "SENSOR FUSION" : "RAW UWB"}</span></div>
        <p className="muted">Sensor fusion combines UWB ranges and IMU data through the EKF. Disable it to publish only the trilaterated UWB position.</p>
        <div className="estimation-controls">
          <label className="switch-row estimation-switch"><input type="checkbox" checked={fusionEnabled} onChange={(event) => setFusionEnabled(event.target.checked)} />Enable sensor fusion</label>
          <div className="save-row"><button type="button" className="primary-button" disabled={estimationSave.kind === "saving"} onClick={saveEstimationConfiguration}>{estimationSave.kind === "saving" ? "Saving…" : "Save position mode"}</button><StatusMessage state={estimationSave} /></div>
        </div>
      </section>

      <section className="panel robot-config-panel">
        <div className="panel-heading"><div><span className="eyebrow">4 · Robots</span><h2>Motor settings</h2></div><span className="retained-tag">PENDING FIRMWARE</span></div>
        <p className="muted">These settings will take effect when supported by the firmware.</p>
        {robotIds.length === 0 ? <p className="empty-message">Robots appear here after their first MQTT message.</p> : (
          <div className="robot-form-grid">
            <label className="field-label">Robot<select aria-label="Robot to configure" value={activeRobotId} onChange={(event) => setActiveRobotId(event.target.value)}>{robotIds.map((id) => <option key={id}>{id}</option>)}</select></label>
            <label className="switch-row"><input type="checkbox" checked={emaEnabled} onChange={(event) => setEmaEnabled(event.target.checked)} />Enable EMA filter</label>
            <label className="field-label">EMA alpha<input aria-label="EMA alpha" disabled={!emaEnabled} type="number" min="0" max="1" step="0.01" value={emaAlpha} onChange={(event) => setEmaAlpha(numberValue(event))} /></label>
            <label className="field-label">Maximum speed<input aria-label="Maximum speed" type="number" min="0" step="0.01" value={maxSpeed} onChange={(event) => setMaxSpeed(numberValue(event))} /></label>
            <div className="save-row"><button type="button" className="primary-button" disabled={robotSave.kind === "saving"} onClick={saveRobotConfiguration}>{robotSave.kind === "saving" ? "Saving…" : "Save settings"}</button><StatusMessage state={robotSave} /></div>
          </div>
        )}
      </section>
    </main>
  );
}
