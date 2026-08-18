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
});

describe("pagina configurazione", () => {
  it("precompila le ancore retained e le salva sul topic corretto", async () => {
    useDashboardStore.setState({
      anchorsConfiguration: {
        robot_antenna_height_m: 0.06,
        anchors: [
          { anchor_id: 0xa001, x: 0, y: 0, z: 2, offset_mm: 0, scale_ppm: 0 },
          { anchor_id: 0xa002, x: 6, y: 0, z: 2, offset_mm: 0, scale_ppm: 0 },
          { anchor_id: 0xa003, x: 6, y: 6, z: 2, offset_mm: 0, scale_ppm: 0 },
          { anchor_id: 0xa004, x: 0, y: 6, z: 2, offset_mm: 0, scale_ppm: 0 },
        ],
      },
    });
    render(<ConfigurationPage />);

    await waitFor(() => expect(screen.getByLabelText("A2 x")).toHaveValue(6));
    fireEvent.click(screen.getByRole("button", { name: "Save anchors" }));
    await waitFor(() => expect(gateway.publish).toHaveBeenCalledWith("/config/anchors", expect.objectContaining({
      robot_antenna_height_m: 0.06,
      anchors: expect.arrayContaining([expect.objectContaining({ anchor_id: 0xa001 })]),
    })));

    fireEvent.click(screen.getByRole("checkbox", { name: "Enable sensor fusion" }));
    fireEvent.click(screen.getByRole("button", { name: "Save position mode" }));
    await waitFor(() => expect(gateway.publish).toHaveBeenCalledWith("/config/estimation", { fusion_enabled: false }));
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
});
