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
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Formazione a V" }));
    fireEvent.change(screen.getByLabelText("Formation leader"), { target: { value: "A1B2C3" } });
    fireEvent.click(screen.getByRole("button", { name: "Applica formazione" }));

    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", {
      program: "vShape",
      leaderId: "A1B2C3",
      anchor: "leader",
      params: {
        interDistanceV: 0.4,
        angleV: -0.79,
        collisionArea: 0.3,
        stabilityThreshold: 0.1,
        electionGrain: 8,
      },
          custom: null,
    });
  });

  it("richiede un leader prima di poter applicare una formazione che lo prevede", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Formazione a V" }));
    expect(screen.getByRole("button", { name: "Applica formazione" })).toBeDisabled();
    expect(screen.getByText("Seleziona un leader prima di applicare questa formazione.")).toBeInTheDocument();
    expect(gateway.publish).not.toHaveBeenCalled();
  });

  it("non richiede un leader per la formazione stop", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: [] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    fireEvent.click(screen.getByRole("button", { name: "Applica formazione" }));

    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", {
      program: "stop",
      leaderId: null,
      anchor: "leader",
      params: {},
          custom: null,
    });
  });

  it("precompila il pannello con la formazione attiva ricevuta dal broker", () => {
    useDashboardStore.setState({
      connectionStatus: "connected",
      robotIds: ["D4E5F6"],
      formation: {
        program: "circleShape",
        leaderId: "D4E5F6",
        anchor: "leader",
        params: { radius: 0.8, collisionArea: 0.3, stabilityThreshold: 0.1 },
      },
    });
    render(<FormationPanel onClose={vi.fn()} />);

    expect(screen.getByText("ATTIVA · CERCHIO")).toBeInTheDocument();
    expect(screen.getByLabelText("Formation leader")).toHaveValue("D4E5F6");
    expect(screen.getByLabelText("Circle radius")).toHaveValue(0.8);
  });

  it("elegge il leader nello sciame quando si sceglie l'ancora automatica", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Cerchio" }));
    fireEvent.click(screen.getByRole("button", { name: "Leader eletto" }));
    fireEvent.click(screen.getByRole("button", { name: "Applica formazione" }));

    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", {
      program: "circleShape",
      leaderId: null,
      anchor: "auto",
      params: {
        radius: 0.6,
        collisionArea: 0.3,
        stabilityThreshold: 0.1,
        electionGrain: 8,
      },
          custom: null,
    });
  });

  it("non chiede un leader quando lo elegge la flotta", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Cerchio" }));
    fireEvent.click(screen.getByRole("button", { name: "Leader eletto" }));

    expect(screen.getByLabelText("Formation leader")).toBeDisabled();
    expect(screen.queryByText("Seleziona un leader prima di applicare questa formazione.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Applica formazione" })).toBeEnabled();
  });

  it("dimentica il leader scelto quando si passa all'elezione", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Cerchio" }));
    fireEvent.change(screen.getByLabelText("Formation leader"), { target: { value: "A1B2C3" } });
    fireEvent.click(screen.getByRole("button", { name: "Leader eletto" }));
    fireEvent.click(screen.getByRole("button", { name: "Applica formazione" }));

    expect(gateway.publish).toHaveBeenCalledWith(
      "/config/formation",
      expect.objectContaining({ anchor: "auto", leaderId: null }),
    );
  });

  it("non mostra l'ancora per le formazioni senza leader", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    expect(screen.queryByLabelText("Formation anchor")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Formation leader")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Applica formazione" }));
    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", {
      program: "stop",
      leaderId: null,
      anchor: "leader",
      params: {},
      custom: null,
    });
  });

  it("pubblica i parametri della formazione dinamica a onda", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Onda circolare" }));
    fireEvent.click(screen.getByRole("button", { name: "Leader eletto" }));
    fireEvent.click(screen.getByRole("button", { name: "Applica formazione" }));

    expect(gateway.publish).toHaveBeenCalledWith(
      "/config/formation",
      expect.objectContaining({
        program: "ringWave",
        anchor: "auto",
        params: expect.objectContaining({ wavePeriod: 6, waveAmplitude: 0.2, waveNumber: 1, radius: 0.6 }),
      }),
    );
  });

  it("pubblica i parametri dell'onda sinusoidale su linea", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Linea sinusoidale" }));
    fireEvent.click(screen.getByRole("button", { name: "Leader eletto" }));
    fireEvent.click(screen.getByRole("button", { name: "Applica formazione" }));

    expect(gateway.publish).toHaveBeenCalledWith(
      "/config/formation",
      expect.objectContaining({
        program: "sineLine",
        anchor: "auto",
        leaderId: null,
        params: expect.objectContaining({
          interDistanceLine: 0.4,
          waveAmplitude: 0.2,
          waveNumber: 1,
          wavePeriod: 6,
        }),
      }),
    );
  });

  it("propone solo il leader scelto o eletto come ancora", () => {
    useDashboardStore.setState({ connectionStatus: "connected", robotIds: ["A1B2C3"] });
    render(<FormationPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Orbita" }));

    const anchors = screen.getByLabelText("Formation anchor");
    expect(anchors.querySelectorAll("button")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Swarm centre" })).not.toBeInTheDocument();
  });

  it("chiama onClose quando si fa clic sul pulsante di chiusura (X)", () => {
    const onClose = vi.fn();
    render(<FormationPanel onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: "Chiudi finestra" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("chiama onClose quando si fa clic sull'overlay di sfondo", () => {
    const onClose = vi.fn();
    const { container } = render(<FormationPanel onClose={onClose} />);

    const overlay = container.querySelector(".modal-overlay");
    expect(overlay).toBeTruthy();
    if (overlay) {
      fireEvent.click(overlay);
    }
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("chiama onClose quando si preme il tasto Escape", () => {
    const onClose = vi.fn();
    render(<FormationPanel onClose={onClose} />);

    fireEvent.keyDown(window, { key: "Escape", code: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
