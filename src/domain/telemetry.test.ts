import { describe, expect, it } from "vitest";
import { parseInboundMqttMessage } from "./telemetry";

describe("normalizzazione della telemetria firmware", () => {
  it("estrae il device ID e la posa dal topic", () => {
    const parsed = parseInboundMqttMessage("/pose/A1B2C3", {
      x_m: 1.2,
      y_m: 3.4,
      heading_rad: 0.5,
      speed_m_s: 0.2,
      position_variance_m2: 0.01,
      timestamp_us: 1234,
    });
    expect(parsed).toMatchObject({ kind: "pose", deviceId: "A1B2C3", payload: { x_m: 1.2, y_m: 3.4 } });
  });

  it("rifiuta ID e payload non validi", () => {
    expect(parseInboundMqttMessage("/pose/invalid", {})).toBeNull();
    expect(parseInboundMqttMessage("/telemetry/A1B2C3", { battery_telemetry: {} })).toBeNull();
  });
});
