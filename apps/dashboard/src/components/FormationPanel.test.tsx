import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FormationPanel } from "./FormationPanel";
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

describe("pannello di formazione dello sciame", () => {
  it("pubblica il comando di formazione con leader e parametri di default", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3", "D4E5F6"] });
    render(<FormationPanel />);

    fireEvent.click(screen.getByRole("button", { name: "V formation" }));
    fireEvent.change(screen.getByLabelText("Formation leader"), { target: { value: "A1B2C3" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply formation" }));

    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", {
      program: "vShape",
      leaderId: "A1B2C3",
      params: { interDistanceV: 0.4, angleV: -0.79, collisionArea: 0.3, stabilityThreshold: 0.1 },
    });
  });

  it("richiede un leader prima di poter applicare una formazione che lo prevede", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel />);

    fireEvent.click(screen.getByRole("button", { name: "V formation" }));
    expect(screen.getByRole("button", { name: "Apply formation" })).toBeDisabled();
    expect(screen.getByText("Pick a leader before applying this formation.")).toBeInTheDocument();
    expect(gateway.publish).not.toHaveBeenCalled();
  });

  it("non richiede un leader per la formazione stop", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: [] });
    render(<FormationPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply formation" }));

    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", {
      program: "stop",
      leaderId: null,
      params: {},
    });
  });

  it("precompila il pannello con la formazione attiva ricevuta dal broker", () => {
    useDashboardStore.setState({
      connectionStatus: "connected",
      robotIds: ["D4E5F6"],
      formation: {
        program: "circleShape",
        leaderId: "D4E5F6",
        params: { radius: 0.8, collisionArea: 0.3, stabilityThreshold: 0.1 },
      },
    });
    render(<FormationPanel />);

    expect(screen.getByText("ACTIVE · CIRCLE")).toBeInTheDocument();
    expect(screen.getByLabelText("Formation leader")).toHaveValue("D4E5F6");
    expect(screen.getByLabelText("Circle radius")).toHaveValue(0.8);
  });
});
