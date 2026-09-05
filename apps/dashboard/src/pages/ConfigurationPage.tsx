import { useEffect, useState } from "react";
import {
  MotorConfigurationSchema,
  OtaConfigurationSchema,
  motorConfigurationTopic,
  otaConfigurationTopic,
  otaCheckTopic,
} from "../../shared/protocol";
import { ArucoMapPanel } from "../components/ArucoMapPanel";
import { useGatewayClient } from "../services/gateway-context";
import { useDashboardStore } from "../store/dashboard-store";

type SaveState = { kind: "idle" | "saving" | "success" | "error"; message?: string };

const MOTOR_SETTINGS_STORAGE_KEY = "project-emerge-motor-settings";

type MotorSettings = { emaEnabled: boolean; emaAlpha: number; maxSpeed: number };

const DEFAULT_MOTOR_SETTINGS: MotorSettings = { emaEnabled: true, emaAlpha: 0.1, maxSpeed: 1 };

function loadMotorSettings(): MotorSettings {
  try {
    const stored = window.localStorage.getItem(MOTOR_SETTINGS_STORAGE_KEY);
    if (!stored) return DEFAULT_MOTOR_SETTINGS;
    const parsed = JSON.parse(stored) as Partial<MotorSettings>;
    return {
      emaEnabled: typeof parsed.emaEnabled === "boolean" ? parsed.emaEnabled : DEFAULT_MOTOR_SETTINGS.emaEnabled,
      emaAlpha: typeof parsed.emaAlpha === "number" ? parsed.emaAlpha : DEFAULT_MOTOR_SETTINGS.emaAlpha,
      maxSpeed: typeof parsed.maxSpeed === "number" ? parsed.maxSpeed : DEFAULT_MOTOR_SETTINGS.maxSpeed,
    };
  } catch {
    return DEFAULT_MOTOR_SETTINGS;
  }
}

function persistMotorSettings(settings: MotorSettings): void {
  try {
    window.localStorage.setItem(MOTOR_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Ignore storage failures (private browsing, quota, ...); the form still holds the value.
  }
}

function numberValue(event: React.ChangeEvent<HTMLInputElement>): number {
  return Number(event.target.value);
}

function defaultOtaServer(): string {
  const configured = import.meta.env.VITE_OTA_SERVER as string | undefined;
  if (configured) return configured;
  const port = window.location.port === "5173" ? "8787" : window.location.port;
  return port ? `${window.location.hostname}:${port}` : window.location.hostname;
}

function StatusMessage({ state }: { state: SaveState }): React.JSX.Element | null {
  if (state.kind === "idle" || state.kind === "saving") return null;
  return <p className={`form-message ${state.kind}`}>{state.message}</p>;
}

export function ConfigurationPage(): React.JSX.Element {
  const gateway = useGatewayClient();
  const robotIds = useDashboardStore((state) => state.robotIds);
  const retainedMotorConfiguration = useDashboardStore((state) => state.motorConfiguration);
  const [emaEnabled, setEmaEnabled] = useState(() => loadMotorSettings().emaEnabled);
  const [emaAlpha, setEmaAlpha] = useState(() => loadMotorSettings().emaAlpha);
  const [maxSpeed, setMaxSpeed] = useState(() => loadMotorSettings().maxSpeed);
  const [robotSave, setRobotSave] = useState<SaveState>({ kind: "idle" });
  const [firmwareVersion, setFirmwareVersion] = useState("");
  const [firmwareFile, setFirmwareFile] = useState<File | null>(null);
  const [otaServer, setOtaServer] = useState(defaultOtaServer);
  const [firmwareUpdate, setFirmwareUpdate] = useState<SaveState>({ kind: "idle" });

  // The broker keeps the fleet-wide motor configuration retained, so it wins over the local copy.
  useEffect(() => {
    if (!retainedMotorConfiguration) return;
    const { ema_filter_alpha, max_speed } = retainedMotorConfiguration.motors;
    setEmaEnabled(ema_filter_alpha !== null);
    if (ema_filter_alpha !== null) setEmaAlpha(ema_filter_alpha);
    setMaxSpeed(max_speed);
  }, [retainedMotorConfiguration]);

  async function saveMotorConfiguration(): Promise<void> {
    const payload = { motors: { ema_filter_alpha: emaEnabled ? emaAlpha : null, max_speed: maxSpeed } };
    const parsed = MotorConfigurationSchema.safeParse(payload);
    if (!parsed.success) {
      setRobotSave({ kind: "error", message: "Motor settings are not valid." });
      return;
    }
    setRobotSave({ kind: "saving" });
    try {
      await gateway.publish(motorConfigurationTopic(), parsed.data);
      persistMotorSettings({ emaEnabled, emaAlpha, maxSpeed });
      setRobotSave({ kind: "success", message: "Settings saved for the whole fleet." });
    } catch (error) {
      setRobotSave({ kind: "error", message: error instanceof Error ? error.message : "Save failed." });
    }
  }

  async function uploadAndUpdateFirmware(): Promise<void> {
    const otaConfiguration = OtaConfigurationSchema.safeParse({ server: otaServer.trim() });
    if (!firmwareFile || !firmwareVersion.trim() || robotIds.length === 0 || !otaConfiguration.success) {
      setFirmwareUpdate({ kind: "error", message: "Choose a reachable OTA server, a version, a .bin firmware file, and wait for at least one robot." });
      return;
    }
    setFirmwareUpdate({ kind: "saving" });
    try {
      const response = await fetch("/api/firmware/latest", {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Firmware-Version": firmwareVersion.trim(),
        },
        body: firmwareFile,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(body?.error ?? "Firmware upload failed.");
      }
      await gateway.publish(otaConfigurationTopic(), otaConfiguration.data);
      await Promise.all(robotIds.map((robotId) => gateway.publish(otaCheckTopic(robotId), {})));
      setFirmwareUpdate({ kind: "success", message: `Firmware uploaded; update requested for all ${robotIds.length} robot${robotIds.length === 1 ? "" : "s"}.` });
    } catch (error) {
      setFirmwareUpdate({ kind: "error", message: error instanceof Error ? error.message : "Firmware update failed." });
    }
  }

  return (
    <main className="configuration-page">
      <section className="page-heading">
        <span className="eyebrow">Fleet</span>
        <h1>Settings</h1>
        <p>Configure robot behavior and deploy firmware updates.</p>
      </section>

      <section className="panel robot-config-panel">
        <div className="panel-heading"><div><span className="eyebrow">1 · Robots</span><h2>Motor settings</h2></div><span className="retained-tag">RETAINED · FLEET</span></div>
        <p className="muted">These settings will take effect when supported by the firmware. They are published retained on <code>/config/motors</code>, so every robot in the fleet picks them up, including robots that connect later.</p>
        <div className="robot-form-grid">
          <label className="switch-row"><input type="checkbox" checked={emaEnabled} onChange={(event) => setEmaEnabled(event.target.checked)} />Enable EMA filter</label>
          <label className="field-label">EMA alpha<input aria-label="EMA alpha" disabled={!emaEnabled} type="number" min="0" max="1" step="0.01" value={emaAlpha} onChange={(event) => setEmaAlpha(numberValue(event))} /></label>
          <label className="field-label">Maximum speed<input aria-label="Maximum speed" type="number" min="0" step="0.01" value={maxSpeed} onChange={(event) => setMaxSpeed(numberValue(event))} /></label>
          <div className="save-row"><button type="button" className="primary-button" disabled={robotSave.kind === "saving"} onClick={saveMotorConfiguration}>{robotSave.kind === "saving" ? "Saving…" : "Save for all robots"}</button><StatusMessage state={robotSave} /></div>
        </div>
      </section>

      <section className="panel firmware-update-panel">
        <div className="panel-heading"><div><span className="eyebrow">2 · Firmware</span><h2>OTA update</h2></div><span className="retained-tag">FLEET ROLLOUT</span></div>
        <p className="muted">Upload a compiled Dropbot <code>.bin</code>, set the dashboard address reachable by robots, then request the update on every discovered robot. The address is retained so each robot can check for future releases.</p>
        <div className="firmware-form-grid">
          <label className="field-label">OTA server<input aria-label="OTA server" type="text" placeholder="192.168.8.1:8787" value={otaServer} onChange={(event) => setOtaServer(event.target.value)} /></label>
          <label className="field-label">Firmware version<input aria-label="Firmware version" type="text" placeholder="0.3.1" value={firmwareVersion} onChange={(event) => setFirmwareVersion(event.target.value)} /></label>
          <label className="field-label">Firmware image<input aria-label="Firmware image" type="file" accept=".bin,application/octet-stream" onChange={(event) => setFirmwareFile(event.target.files?.[0] ?? null)} /></label>
          <div className="save-row"><button type="button" className="primary-button" disabled={firmwareUpdate.kind === "saving" || robotIds.length === 0} onClick={uploadAndUpdateFirmware}>{firmwareUpdate.kind === "saving" ? "Updating…" : "Upload & update fleet"}</button><StatusMessage state={firmwareUpdate} /></div>
        </div>
      </section>

      <ArucoMapPanel />
    </main>
  );
}
