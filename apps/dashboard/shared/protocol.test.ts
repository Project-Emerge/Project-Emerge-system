import { describe, expect, it } from "vitest";
import {
  arucoMapTopic,
  formationTopic,
  isAllowedConfigurationTopic,
  isMotorCommandTopic,
  isOtaCheckTopic,
  isTransientCommandTopic,
  motorCommandTopic,
  otaConfigurationTopic,
  otaCheckTopic,
  motorConfigurationTopic,
  validateClientPublication,
  validateConfigurationPublication,
} from "./protocol.js";

describe("contratti di configurazione MQTT", () => {
  it("accetta soltanto i topic retained previsti", () => {
    expect(isAllowedConfigurationTopic("/config/ota")).toBe(true);
    expect(isAllowedConfigurationTopic("/config/motors")).toBe(true);
    expect(isAllowedConfigurationTopic("/config/aruco-map")).toBe(true);
    expect(isAllowedConfigurationTopic("/config/formation")).toBe(true);
    expect(isAllowedConfigurationTopic("/motors/A1B2C3")).toBe(false);
    expect(isAllowedConfigurationTopic("/config/robots/A1B2C3")).toBe(false);
  });

  it("costruisce e valida il comando di formazione dello sciame", () => {
    expect(formationTopic()).toBe("/config/formation");
    expect(validateConfigurationPublication("/config/formation", {
      program: "vShape",
      leaderId: "A1B2C3",
      params: { interDistanceV: 0.4, angleV: -0.78, collisionArea: 0.3, stabilityThreshold: 0.1 },
    })).toBeNull();
    expect(validateConfigurationPublication("/config/formation", {
      program: "stop",
      leaderId: null,
      params: {},
    })).toBeNull();
    expect(validateConfigurationPublication("/config/formation", {
      program: "octagonShape",
      leaderId: null,
      params: {},
    })).not.toBeNull();
    expect(validateConfigurationPublication("/config/formation", {
      program: "vShape",
      leaderId: "not-an-id",
      params: {},
    })).not.toBeNull();
    expect(validateConfigurationPublication("/config/formation", {
      program: "vShape",
      leaderId: null,
      params: { angleV: "fast" },
    })).not.toBeNull();
  });

  it("costruisce e valida la mappatura marker ArUco -> robot", () => {
    expect(arucoMapTopic()).toBe("/config/aruco-map");
    expect(validateConfigurationPublication("/config/aruco-map", {
      "0": "A1B2C3",
      "12": "D4E5F6",
    })).toBeNull();
    expect(validateConfigurationPublication("/config/aruco-map", {
      "50": "A1B2C3",
    })).not.toBeNull();
    expect(validateConfigurationPublication("/config/aruco-map", {
      "07": "A1B2C3",
    })).not.toBeNull();
    expect(validateConfigurationPublication("/config/aruco-map", {
      "0": "not-an-id",
    })).not.toBeNull();
    expect(validateConfigurationPublication("/config/aruco-map", {
      "0": "A1B2C3",
      "1": "A1B2C3",
    })).not.toBeNull();
  });

  it("costruisce e valida la configurazione motore condivisa dalla flotta", () => {
    expect(motorConfigurationTopic()).toBe("/config/motors");
    expect(validateConfigurationPublication("/config/motors", {
      motors: { ema_filter_alpha: 0.1, max_speed: 1 },
    })).toBeNull();
    expect(validateConfigurationPublication("/config/motors", {
      motors: { ema_filter_alpha: 4, max_speed: -1 },
    })).not.toBeNull();
    expect(validateConfigurationPublication("/config/ota", { server: "192.168.8.1:8787" })).toBeNull();
    expect(validateConfigurationPublication("/config/ota", { server: "http://192.168.8.1" })).not.toBeNull();
  });

  it("costruisce il comando OTA indirizzato a un solo robot", () => {
    expect(otaConfigurationTopic()).toBe("/config/ota");
    expect(otaCheckTopic("A1B2C3")).toBe("/ota/check/A1B2C3");
    expect(isOtaCheckTopic("/ota/check/A1B2C3")).toBe(true);
    expect(isOtaCheckTopic("/ota/check/not-an-id")).toBe(false);
  });

  it("valida i comandi motore normalizzati come transitori", () => {
    expect(motorCommandTopic("A1B2C3")).toBe("/motors/A1B2C3");
    expect(isMotorCommandTopic("/motors/A1B2C3")).toBe(true);
    expect(isTransientCommandTopic("/motors/A1B2C3")).toBe(true);
    expect(validateClientPublication("/motors/A1B2C3", {
      Move: { left: 0.8, right: -0.4 },
    })).toBeNull();
    expect(validateClientPublication("/motors/A1B2C3", "Stop")).toBeNull();
    expect(validateClientPublication("/motors/A1B2C3", {
      Move: { left: 1.2, right: 0 },
    })).not.toBeNull();
    expect(validateClientPublication("/motors/A1B2C3", "Stopped")).not.toBeNull();
    expect(validateClientPublication("/motors/A1B2C3", {
      Motoring: { left: 0.8, right: -0.4 },
    })).not.toBeNull();
  });
});
