import { describe, expect, it } from "vitest";
import {
  arucoMapTopic,
  FormationCommandSchema,
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
  CUSTOM_FORMULA_MAX_LENGTH,
  CUSTOM_MAX_COORDINATE,
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
    // The anchor is optional so that commands retained before it existed keep working.
    expect(FormationCommandSchema.parse({
      program: "circleShape",
      leaderId: null,
      params: {},
    }).anchor).toBe("leader");
    expect(validateConfigurationPublication("/config/formation", {
      program: "circleShape",
      leaderId: null,
      anchor: "auto",
      params: { radius: 0.6 },
    })).toBeNull();
    expect(validateConfigurationPublication("/config/formation", {
      program: "circleShape",
      leaderId: null,
      anchor: "swarm",
      params: {},
    })).not.toBeNull();
    // The time-varying programs must be accepted too.
    for (const program of [
      "orbitCircle",
      "breathingCircle",
      "ringWave",
      "sineLine",
    ]) {
      expect(validateConfigurationPublication("/config/formation", {
        program,
        leaderId: null,
        anchor: "auto",
        params: {},
      })).toBeNull();
    }
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

  it("accetta ogni forma di geometria disegnata", () => {
    const geometries = [
      { kind: "points", points: [[0, 0.5], [0.5, 0], [-0.5, 0]], closed: true, label: "Triangle" },
      { kind: "points", points: [[0, 0.5]] },
      { kind: "cartesian", x: "0.3*i", y: "0.2*sin(t)" },
      { kind: "polar", r: "0.5", theta: "2*pi*i/n + t", label: "Star" },
    ];
    geometries.forEach((custom) => {
      const parsed = FormationCommandSchema.safeParse({
        program: "custom", leaderId: null, anchor: "auto", params: {}, custom,
      });
      expect(parsed.success, JSON.stringify(custom)).toBe(true);
    });
  });

  it("rifiuta una formazione custom senza la geometria che la definisce", () => {
    // Il runtime ricadrebbe su un anello silenziosamente: meglio non pubblicarla affatto.
    expect(validateConfigurationPublication(formationTopic(), {
      program: "custom", leaderId: null, anchor: "auto", params: {},
    })).toBe("A custom formation must carry the geometry it is built from.");
    expect(validateConfigurationPublication(formationTopic(), {
      program: "custom", leaderId: null, anchor: "auto", params: {}, custom: null,
    })).not.toBeNull();
  });

  it("rifiuta le geometrie malformate", () => {
    const rejected = [
      { kind: "points", points: [] },
      { kind: "points", points: [[0, 0.5, 0.1]] },
      { kind: "points", points: [["a", "b"]] },
      // Oltre il tetto operatore: il runtime ha comunque il suo, non alzabile da un messaggio.
      { kind: "points", points: [[0, CUSTOM_MAX_COORDINATE + 1]] },
      { kind: "points", points: [[0, Number.NaN]] },
      { kind: "cartesian", x: "0.3*i" },
      { kind: "cartesian", x: "", y: "0" },
      { kind: "cartesian", x: "1".repeat(CUSTOM_FORMULA_MAX_LENGTH + 1), y: "0" },
      { kind: "polar", r: "0.5" },
      { kind: "spline", points: [[0, 0.5]] },
      { points: [[0, 0.5]] },
    ];
    rejected.forEach((custom) => {
      const parsed = FormationCommandSchema.safeParse({
        program: "custom", leaderId: null, anchor: "auto", params: {}, custom,
      });
      expect(parsed.success, JSON.stringify(custom)).toBe(false);
    });
  });

  it("continua ad accettare un comando retained pubblicato prima delle formazioni custom", () => {
    // Il topic e' retained: un payload salvato dal broker mesi fa deve ancora essere leggibile.
    const parsed = FormationCommandSchema.safeParse({
      program: "vShape", leaderId: "A1B2C3", params: { interDistanceV: 0.4 },
    });
    expect(parsed.success).toBe(true);
    expect(parsed.success && parsed.data.anchor).toBe("leader");
    expect(parsed.success && parsed.data.custom).toBeUndefined();
  });

  it("ignora una geometria allegata a un programma compilato", () => {
    // Il runtime la memorizza ma nessun programma tranne `custom` la legge: e' inerte,
    // non un errore, cosi' un operatore puo' passare a circleShape e tornare indietro.
    expect(validateConfigurationPublication(formationTopic(), {
      program: "circleShape",
      leaderId: "A1B2C3",
      anchor: "leader",
      params: { radius: 0.6 },
      custom: { kind: "polar", r: "0.5", theta: "2*pi*i/n" },
    })).toBeNull();
  });
});
