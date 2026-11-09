import { describe, expect, it } from "vitest";
import { deriveFleetSnapshot, describeFleetForPrompt } from "./fleet-snapshot.js";
import type { GatewayMqttMessage } from "../shared/protocol.js";

function snapshotsOf(entries: Record<string, unknown>): Map<string, GatewayMqttMessage> {
  return new Map(
    Object.entries(entries).map(([topic, payload]) => [topic, { topic, payload, receivedAt: 0 }]),
  );
}

const pose = { x_m: 0.1, y_m: 0.2, heading_rad: 0, speed_m_s: 0, timestamp_us: 0 };

describe("stato della flotta per l'agente", () => {
  it("elenca i robot posizionati e quelli soltanto sentiti", () => {
    const fleet = deriveFleetSnapshot(snapshotsOf({
      "/pose/D4E5F6": pose,
      "/pose/A1B2C3": pose,
      // Un robot che manda telemetria ma non posizione esiste: l'agente deve poterlo nominare
      // senza pero' poterlo usare come leader.
      "/telemetry/0A0B0C": {},
      "/imu/A1B2C3": {},
    }));
    expect(fleet.posedRobotIds).toEqual(["A1B2C3", "D4E5F6"]);
    expect(fleet.robotIds).toEqual(["0A0B0C", "A1B2C3", "D4E5F6"]);
  });

  it("ignora i topic che non nominano un robot", () => {
    const fleet = deriveFleetSnapshot(snapshotsOf({
      "/pose/D4E5F6": pose,
      "/config/ota": { server: "192.168.8.1:8787" },
      "/config/aruco-map": { "3": "D4E5F6" },
      "/pose/nonvalido": pose,
      "/neighbors/D4E5F6": ["A1B2C3"],
    }));
    expect(fleet.robotIds).toEqual(["D4E5F6"]);
    expect(fleet.posedRobotIds).toEqual(["D4E5F6"]);
  });

  it("legge la formazione attiva dal payload retained", () => {
    const fleet = deriveFleetSnapshot(snapshotsOf({
      "/config/formation": {
        program: "circleShape",
        leaderId: "D4E5F6",
        anchor: "leader",
        params: { radius: 0.8 },
      },
    }));
    expect(fleet.activeFormation?.program).toBe("circleShape");
    expect(fleet.activeFormation?.params).toEqual({ radius: 0.8 });
  });

  it("tratta una formazione retained illeggibile come nessuna formazione", () => {
    // Un topic che questo gateway non sa leggere non deve far cadere l'intera chat.
    const fleet = deriveFleetSnapshot(snapshotsOf({
      "/config/formation": { program: "octagonShape", leaderId: null, params: {} },
    }));
    expect(fleet.activeFormation).toBeNull();
  });

  it("riporta nessuna formazione quando il topic non e' mai stato pubblicato", () => {
    expect(deriveFleetSnapshot(snapshotsOf({})).activeFormation).toBeNull();
  });

  it("descrive una flotta vuota dicendo che non si puo' costruire nulla", () => {
    const prompt = describeFleetForPrompt(deriveFleetSnapshot(snapshotsOf({})));
    expect(prompt).toContain("No robot has a known position");
    expect(prompt).toContain("No formation is running");
  });

  it("avverte l'agente di non usare come leader un robot senza posizione", () => {
    const prompt = describeFleetForPrompt(deriveFleetSnapshot(snapshotsOf({
      "/pose/A1B2C3": pose,
      "/telemetry/0A0B0C": {},
    })));
    expect(prompt).toContain("A1B2C3");
    expect(prompt).toContain("0A0B0C");
    expect(prompt).toContain("Do not use these as a leader");
  });

  it("include la geometria disegnata quando ne e' attiva una", () => {
    const prompt = describeFleetForPrompt(deriveFleetSnapshot(snapshotsOf({
      "/pose/A1B2C3": pose,
      "/config/formation": {
        program: "custom",
        leaderId: null,
        anchor: "auto",
        params: { customScale: 1.5 },
        custom: { kind: "polar", r: "0.5", theta: "2*pi*i/n", label: "Stella" },
      },
    })));
    expect(prompt).toContain("custom (anchor auto)");
    expect(prompt).toContain("customScale=1.5");
    expect(prompt).toContain("Stella");
  });
});
