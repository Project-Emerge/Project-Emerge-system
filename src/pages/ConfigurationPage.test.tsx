import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfigurationPage } from "./ConfigurationPage";
import { useDashboardStore } from "../store/dashboard-store";

const gateway = vi.hoisted(() => ({ publish: vi.fn().mockResolvedValue(undefined) }));

vi.mock("../services/gateway-context", () => ({
  useGatewayClient: () => gateway,
}));

afterEach(() => {
  gateway.publish.mockClear();
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
});
