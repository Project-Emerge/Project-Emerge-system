import { describe, expect, it } from "vitest";
import {
  isAllowedConfigurationTopic,
  robotConfigurationTopic,
  validateConfigurationPublication,
} from "./protocol.js";

describe("contratti di configurazione MQTT", () => {
  it("accetta soltanto i topic retained previsti", () => {
    expect(isAllowedConfigurationTopic("/config/anchors")).toBe(true);
    expect(isAllowedConfigurationTopic("/config/estimation")).toBe(true);
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
    expect(validateConfigurationPublication("/config/estimation", { fusion_enabled: false })).toBeNull();
    expect(validateConfigurationPublication("/config/estimation", { fusion_enabled: "false" })).not.toBeNull();
  });
});
