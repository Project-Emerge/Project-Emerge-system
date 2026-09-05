import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfigurationPage } from "./ConfigurationPage";
import { useDashboardStore } from "../store/dashboard-store";

const gateway = vi.hoisted(() => ({ publish: vi.fn().mockResolvedValue(undefined) }));

vi.mock("../services/gateway-context", () => ({
  useGatewayClient: () => gateway,
}));

afterEach(() => {
  cleanup();
  gateway.publish.mockClear();
  vi.unstubAllGlobals();
  useDashboardStore.getState().resetForTests();
  window.localStorage.clear();
});

describe("pagina configurazione", () => {
  it("applica le impostazioni motore a tutti i robot della flotta e le rende persistenti", async () => {
    window.localStorage.clear();
    useDashboardStore.setState({ robotIds: ["A1B2C3", "D4E5F6"] });
    render(<ConfigurationPage />);

    fireEvent.change(screen.getByLabelText("Maximum speed"), { target: { value: "1.25" } });
    fireEvent.click(screen.getByRole("button", { name: "Save for all robots" }));

    await waitFor(() => expect(gateway.publish).toHaveBeenCalledWith("/config/robots/A1B2C3", {
      motors: { ema_filter_alpha: 0.1, max_speed: 1.25 },
    }));
    expect(gateway.publish).toHaveBeenCalledWith("/config/robots/D4E5F6", {
      motors: { ema_filter_alpha: 0.1, max_speed: 1.25 },
    });
    expect(screen.getByText("Settings saved for all 2 robots.")).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("project-emerge-motor-settings")!)).toEqual({
      emaEnabled: true,
      emaAlpha: 0.1,
      maxSpeed: 1.25,
    });
  });

  it("ricarica le impostazioni motore persistite all'apertura della pagina", () => {
    window.localStorage.setItem("project-emerge-motor-settings", JSON.stringify({
      emaEnabled: false,
      emaAlpha: 0.3,
      maxSpeed: 0.6,
    }));
    useDashboardStore.setState({ robotIds: ["A1B2C3"] });
    render(<ConfigurationPage />);

    expect(screen.getByLabelText("Maximum speed")).toHaveValue(0.6);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    window.localStorage.clear();
  });

  it("carica il firmware e richiede l'aggiornamento OTA di tutti i robot", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ version: "0.3.1" }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    useDashboardStore.setState({ robotIds: ["A1B2C3", "D4E5F6"] });
    render(<ConfigurationPage />);

    fireEvent.change(screen.getByLabelText("OTA server"), { target: { value: "192.168.8.1:8787" } });
    fireEvent.change(screen.getByLabelText("Firmware version"), { target: { value: "0.3.1" } });
    fireEvent.change(screen.getByLabelText("Firmware image"), { target: { files: [new File(["firmware"], "dropbot.bin", { type: "application/octet-stream" })] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload & update fleet" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/firmware/latest", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-Firmware-Version": "0.3.1" }),
    })));
    await waitFor(() => expect(gateway.publish).toHaveBeenNthCalledWith(1, "/config/ota", { server: "192.168.8.1:8787" }));
    await waitFor(() => expect(gateway.publish).toHaveBeenCalledWith("/ota/check/A1B2C3", {}));
    expect(gateway.publish).toHaveBeenCalledWith("/ota/check/D4E5F6", {});
    expect(screen.getByText("Firmware uploaded; update requested for all 2 robots.")).toBeInTheDocument();
  });

  it("compone il pannello di mappatura marker ArUco", () => {
    render(<ConfigurationPage />);
    expect(screen.getByText("Marker mapping")).toBeInTheDocument();
  });
});
