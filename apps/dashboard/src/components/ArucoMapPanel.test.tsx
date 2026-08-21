import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ArucoMapPanel } from "./ArucoMapPanel";
import { useDashboardStore } from "../store/dashboard-store";

const gateway = vi.hoisted(() => ({ publish: vi.fn().mockResolvedValue(undefined) }));

vi.mock("../services/gateway-context", () => ({
  useGatewayClient: () => gateway,
}));

afterEach(() => {
  cleanup();
  gateway.publish.mockClear();
  useDashboardStore.getState().resetForTests();
});

function fillAndSubmit(markerId: string, robotId: string): void {
  fireEvent.change(screen.getByLabelText("ArUco marker ID"), { target: { value: markerId } });
  fireEvent.change(screen.getByLabelText("Robot ID"), { target: { value: robotId } });
  fireEvent.click(screen.getByRole("button", { name: "Add mapping" }));
}

describe("pannello di mappatura marker ArUco", () => {
  it("pubblica la mappa retained con la nuova coppia marker -> robot", async () => {
    useDashboardStore.setState({ arucoMap: { "1": "A1B2C3" }, robotIds: ["A1B2C3", "D4E5F6"] });
    render(<ArucoMapPanel />);

    fillAndSubmit("7", "d4e5f6");

    await waitFor(() => expect(gateway.publish).toHaveBeenCalledWith("/config/aruco-map", {
      "1": "A1B2C3",
      "7": "D4E5F6",
    }));
  });

  it("non pubblica con un ID marker fuori intervallo o un ID robot malformato", () => {
    render(<ArucoMapPanel />);

    fireEvent.change(screen.getByLabelText("ArUco marker ID"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("Robot ID"), { target: { value: "A1B2C3" } });
    expect(screen.getByRole("button", { name: "Add mapping" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("ArUco marker ID"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Robot ID"), { target: { value: "not-an-id" } });
    expect(screen.getByRole("button", { name: "Add mapping" })).toBeDisabled();
    expect(gateway.publish).not.toHaveBeenCalled();
  });

  it("blocca un ID robot gia' assegnato a un altro marker", () => {
    useDashboardStore.setState({ arucoMap: { "1": "A1B2C3" } });
    render(<ArucoMapPanel />);

    fireEvent.change(screen.getByLabelText("ArUco marker ID"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Robot ID"), { target: { value: "A1B2C3" } });

    expect(screen.getByText("Robot A1B2C3 is already mapped to marker 1. Remove that mapping first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add mapping" })).toBeDisabled();
    expect(gateway.publish).not.toHaveBeenCalled();
  });

  it("permette di riassegnare un marker esistente allo stesso ID", async () => {
    useDashboardStore.setState({ arucoMap: { "1": "A1B2C3" } });
    render(<ArucoMapPanel />);

    fireEvent.change(screen.getByLabelText("ArUco marker ID"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Robot ID"), { target: { value: "D4E5F6" } });
    expect(screen.getByText("Marker 1 is currently mapped to A1B2C3 — saving will reassign it to D4E5F6.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add mapping" }));
    await waitFor(() => expect(gateway.publish).toHaveBeenCalledWith("/config/aruco-map", { "1": "D4E5F6" }));
  });

  it("rimuove una mappatura pubblicando la mappa aggiornata", async () => {
    useDashboardStore.setState({ arucoMap: { "1": "A1B2C3", "2": "D4E5F6" } });
    render(<ArucoMapPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Remove mapping for marker 1" }));

    await waitFor(() => expect(gateway.publish).toHaveBeenCalledWith("/config/aruco-map", { "2": "D4E5F6" }));
  });
});
