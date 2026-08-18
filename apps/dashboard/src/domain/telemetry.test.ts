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

  it("mantiene distinto lo stato Motoring dai comandi Move", () => {
    const telemetry = {
      motor_telemetry: { Motoring: { left: 0.6, right: 0.4 } },
      battery_telemetry: { voltage: 7.6, current: 0.3, temperature: 27, is_charging: false, state_of_charge: 82 },
      imu_telemetry: {
        timestamp_us: 1,
        raw: { accelerometer: [0, 0, 0], gyroscope: [0, 0, 0], magnetometer: [0, 0, 0], temperature: 25 },
        filtered: {
          accelerometer: [0, 0, 0],
          gyroscope: [0, 0, 0],
          magnetometer: [0, 0, 0],
          linear_acceleration: [0, 0, 0],
          quaternion: [0, 0, 0, 1],
          roll: 0,
          pitch: 0,
          heading: 0,
          is_stationary: false,
        },
      },
      network_telemetry: { rssi: -50, ip_address: "192.168.8.20" },
    };

    expect(parseInboundMqttMessage("/telemetry/A1B2C3", telemetry)).toMatchObject({
      kind: "telemetry",
      payload: { motor_telemetry: { Motoring: { left: 0.6, right: 0.4 } } },
    });
    expect(parseInboundMqttMessage("/telemetry/A1B2C3", {
      ...telemetry,
      motor_telemetry: { Move: { left: 0.6, right: 0.4 } },
    })).toBeNull();
  });
});
