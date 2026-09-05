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

  it("accetta pose Vision senza inventare una varianza", () => {
    const parsed = parseInboundMqttMessage("/pose/A1B2C3", {
      x_m: 1.2,
      y_m: 3.4,
      z_m: 0.1,
      heading_rad: 0.5,
      speed_m_s: 0.2,
      timestamp_us: 1234,
      quality: 0.8,
      visible_by: ["cam_0", "cam_1"],
    });
    expect(parsed).toMatchObject({
      kind: "pose",
      deviceId: "A1B2C3",
      payload: {
        position_variance_m2: null,
        z_m: 0.1,
        quality: 0.8,
        visible_by: ["cam_0", "cam_1"],
      },
    });
  });

  it("estrae la lista dei vicini escludendo il mittente", () => {
    const parsed = parseInboundMqttMessage("/neighbors/A1B2C3", ["A1B2C3", "D4E5F6"]);
    expect(parsed).toEqual({ kind: "neighbors", deviceId: "A1B2C3", payload: ["D4E5F6"] });
  });

  it("accetta un vicinato vuoto ma rifiuta ID vicini malformati", () => {
    expect(parseInboundMqttMessage("/neighbors/A1B2C3", [])).toEqual({
      kind: "neighbors",
      deviceId: "A1B2C3",
      payload: [],
    });
    expect(parseInboundMqttMessage("/neighbors/A1B2C3", ["nope"])).toBeNull();
    expect(parseInboundMqttMessage("/neighbors/invalid", ["A1B2C3"])).toBeNull();
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

  it("interpreta la configurazione motore condivisa dalla flotta", () => {
    expect(parseInboundMqttMessage("/config/motors", { motors: { ema_filter_alpha: 0.1, max_speed: 1 } })).toMatchObject({
      kind: "motor-config",
      payload: { motors: { ema_filter_alpha: 0.1, max_speed: 1 } },
    });
    expect(parseInboundMqttMessage("/config/motors", { motors: { ema_filter_alpha: 2, max_speed: 1 } })).toBeNull();
    expect(parseInboundMqttMessage("/config/robots/A1B2C3", { motors: { ema_filter_alpha: 0.1, max_speed: 1 } })).toBeNull();
  });

  it("interpreta la mappatura marker ArUco -> robot", () => {
    expect(parseInboundMqttMessage("/config/aruco-map", { "0": "A1B2C3", "12": "D4E5F6" })).toMatchObject({
      kind: "aruco-map",
      payload: { "0": "A1B2C3", "12": "D4E5F6" },
    });
    expect(parseInboundMqttMessage("/config/aruco-map", { "50": "A1B2C3" })).toBeNull();
    expect(parseInboundMqttMessage("/config/aruco-map", { "0": "not-an-id" })).toBeNull();
  });

  it("interpreta il comando di formazione dello sciame", () => {
    expect(parseInboundMqttMessage("/config/formation", {
      program: "vShape",
      leaderId: "A1B2C3",
      params: { interDistanceV: 0.4, angleV: -0.78 },
    })).toMatchObject({
      kind: "formation",
      payload: { program: "vShape", leaderId: "A1B2C3", params: { interDistanceV: 0.4 } },
    });
    expect(parseInboundMqttMessage("/config/formation", {
      program: "unknownShape",
      leaderId: null,
      params: {},
    })).toBeNull();
  });
});
