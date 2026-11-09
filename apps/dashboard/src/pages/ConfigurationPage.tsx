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
import { useLocale } from "../services/locale-context";
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
  const { t } = useLocale();
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
      setRobotSave({ kind: "error", message: t.configPage.motorSettingsInvalid });
      return;
    }
    setRobotSave({ kind: "saving" });
    try {
      await gateway.publish(motorConfigurationTopic(), parsed.data);
      persistMotorSettings({ emaEnabled, emaAlpha, maxSpeed });
      setRobotSave({ kind: "success", message: t.configPage.motorSettingsSaved });
    } catch (error) {
      setRobotSave({ kind: "error", message: error instanceof Error ? error.message : t.configPage.motorSaveFailed });
    }
  }

  async function uploadAndUpdateFirmware(): Promise<void> {
    const otaConfiguration = OtaConfigurationSchema.safeParse({ server: otaServer.trim() });
    if (!firmwareFile || !firmwareVersion.trim() || robotIds.length === 0 || !otaConfiguration.success) {
      setFirmwareUpdate({ kind: "error", message: t.configPage.firmwareFormInvalid });
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
        throw new Error(body?.error ?? t.configPage.firmwareUploadFailed);
      }
      await gateway.publish(otaConfigurationTopic(), otaConfiguration.data);
      await Promise.all(robotIds.map((robotId) => gateway.publish(otaCheckTopic(robotId), {})));
      setFirmwareUpdate({ kind: "success", message: t.configPage.firmwareUploadSuccess(robotIds.length) });
    } catch (error) {
      setFirmwareUpdate({ kind: "error", message: error instanceof Error ? error.message : t.configPage.firmwareUpdateFailed });
    }
  }

  return (
    <main className="configuration-page">
      <section className="page-heading">
        <span className="eyebrow">{t.configPage.fleet}</span>
        <h1>{t.configPage.settings}</h1>
        <p>{t.configPage.subtitle}</p>
      </section>

      <section className="panel robot-config-panel">
        <div className="panel-heading"><div><span className="eyebrow">{t.configPage.motorSectionEyebrow}</span><h2>{t.configPage.motorSectionTitle}</h2></div><span className="retained-tag">{t.configPage.retainedFleetTag}</span></div>
        <p className="muted">{t.configPage.motorSectionDesc}</p>
        <div className="robot-form-grid">
          <label className="switch-row"><input type="checkbox" checked={emaEnabled} onChange={(event) => setEmaEnabled(event.target.checked)} />{t.configPage.enableEma}</label>
          <label className="field-label">{t.configPage.emaAlpha}<input aria-label="EMA alpha" disabled={!emaEnabled} type="number" min="0" max="1" step="0.01" value={emaAlpha} onChange={(event) => setEmaAlpha(numberValue(event))} /></label>
          <label className="field-label">{t.configPage.maxSpeed}<input aria-label="Maximum speed" type="number" min="0" step="0.01" value={maxSpeed} onChange={(event) => setMaxSpeed(numberValue(event))} /></label>
          <div className="save-row"><button type="button" className="primary-button" disabled={robotSave.kind === "saving"} onClick={saveMotorConfiguration}>{robotSave.kind === "saving" ? t.configPage.savingButton : t.configPage.saveMotorsButton}</button><StatusMessage state={robotSave} /></div>
        </div>
      </section>

      <section className="panel firmware-update-panel">
        <div className="panel-heading"><div><span className="eyebrow">{t.configPage.firmwareSectionEyebrow}</span><h2>{t.configPage.firmwareSectionTitle}</h2></div><span className="retained-tag">{t.configPage.fleetRolloutTag}</span></div>
        <p className="muted">{t.configPage.firmwareSectionDesc}</p>
        <div className="firmware-form-grid">
          <label className="field-label">{t.configPage.otaServer}<input aria-label="OTA server" type="text" placeholder="192.168.8.1:8787" value={otaServer} onChange={(event) => setOtaServer(event.target.value)} /></label>
          <label className="field-label">{t.configPage.firmwareVersion}<input aria-label="Firmware version" type="text" placeholder="0.3.1" value={firmwareVersion} onChange={(event) => setFirmwareVersion(event.target.value)} /></label>
          <label className="field-label">{t.configPage.firmwareImage}<input aria-label="Firmware image" type="file" accept=".bin,application/octet-stream" onChange={(event) => setFirmwareFile(event.target.files?.[0] ?? null)} /></label>
          <div className="save-row"><button type="button" className="primary-button" disabled={firmwareUpdate.kind === "saving" || robotIds.length === 0} onClick={uploadAndUpdateFirmware}>{firmwareUpdate.kind === "saving" ? t.configPage.updatingButton : t.configPage.uploadButton}</button><StatusMessage state={firmwareUpdate} /></div>
        </div>
      </section>

      <ArucoMapPanel />
    </main>
  );
}
