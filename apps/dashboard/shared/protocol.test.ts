import { describe, expect, it } from "vitest";
import {
  isAllowedConfigurationTopic,
  isMotorCommandTopic,
  isOtaCheckTopic,
  isTransientCommandTopic,
  motorCommandTopic,
  otaConfigurationTopic,
  otaCheckTopic,
  robotConfigurationTopic,
  validateClientPublication,
  validateConfigurationPublication,
} from "./protocol.js";

describe("contratti di configurazione MQTT", () => {
  it("accetta soltanto i topic retained previsti", () => {
    expect(isAllowedConfigurationTopic("/config/ota")).toBe(true);
    expect(isAllowedConfigurationTopic("/config/robots/A1B2C3")).toBe(true);
    expect(isAllowedConfigurationTopic("/motors/A1B2C3")).toBe(false);
    expect(isAllowedConfigurationTopic("/config/robots/not-an-id")).toBe(false);
  });

  it("costruisce e valida la configurazione futura del robot", () => {
    expect(robotConfigurationTopic("A1B2C3")).toBe("/config/robots/A1B2C3");
    expect(validateConfigurationPublication("/config/robots/A1B2C3", {
      motors: { ema_filter_alpha: 0.1, max_speed: 1 },
    })).toBeNull();
    expect(validateConfigurationPublication("/config/robots/A1B2C3", {
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
