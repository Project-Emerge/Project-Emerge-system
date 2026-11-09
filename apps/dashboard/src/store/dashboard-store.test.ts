import { beforeEach, describe, expect, it } from "vitest";
import { useDashboardStore } from "./dashboard-store";

function ingest(topic: string, payload: unknown): void {
  useDashboardStore.getState().ingestMqttMessage({ topic, payload, receivedAt: Date.now() });
}

describe("stato del vicinato nello store", () => {
  beforeEach(() => useDashboardStore.getState().resetForTests());

  it("memorizza i vicini per robot", () => {
    ingest("/neighbors/A1B2C3", ["D4E5F6"]);
    expect(useDashboardStore.getState().neighbors).toEqual({ A1B2C3: ["D4E5F6"] });
  });

  it("non ricrea la mappa quando il vicinato viene ripubblicato invariato", () => {
    ingest("/neighbors/A1B2C3", ["D4E5F6"]);
    const first = useDashboardStore.getState().neighbors;
    ingest("/neighbors/A1B2C3", ["D4E5F6"]);
    expect(useDashboardStore.getState().neighbors).toBe(first);
  });

  it("aggiorna la mappa quando il vicinato cambia", () => {
    ingest("/neighbors/A1B2C3", ["D4E5F6"]);
    const first = useDashboardStore.getState().neighbors;
    ingest("/neighbors/A1B2C3", ["D4E5F6", "AABBCC"]);
    expect(useDashboardStore.getState().neighbors).not.toBe(first);
    expect(useDashboardStore.getState().neighbors.A1B2C3).toEqual(["D4E5F6", "AABBCC"]);
  });

  it("non crea robot nella barra laterale a partire dal solo vicinato", () => {
    ingest("/neighbors/A1B2C3", ["D4E5F6"]);
    expect(useDashboardStore.getState().robotIds).toEqual([]);
  });
});
