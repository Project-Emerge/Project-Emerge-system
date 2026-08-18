import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RobotDetailsSidebar } from "./RobotDetailsSidebar";
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

describe("sidebar robot", () => {
  it("resta visibile e apre la telemetria del robot selezionato", () => {
    useDashboardStore.setState({
      robots: {
        A1B2C3: {
          id: "A1B2C3",
          lastSeenAt: Date.now(),
          telemetry: telemetryWithBattery(72),
        },
        D4E5F6: { id: "D4E5F6", lastSeenAt: Date.now() },
      },
      robotIds: ["A1B2C3", "D4E5F6"],
    });
    render(<RobotDetailsSidebar />);

    const firstRobot = screen.getByRole("button", { name: /A1B2C3/ });
    expect(screen.getByRole("complementary", { name: "Reachable robots" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /D4E5F6/ })).toBeInTheDocument();
    expect(firstRobot).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(firstRobot);
    expect(firstRobot).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("72%")).toBeInTheDocument();
    expect(screen.getByText("Pack voltage")).toBeInTheDocument();

    fireEvent.click(firstRobot);
    expect(firstRobot).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Pack voltage")).not.toBeInTheDocument();
  });

  it("aggiorna i valori aperti quando arriva nuova telemetria", () => {
    const receivedAt = Date.now();
    useDashboardStore.getState().ingestMqttMessage({
      topic: "/telemetry/A1B2C3",
      payload: telemetryWithBattery(72),
      receivedAt,
    });
    render(<RobotDetailsSidebar />);
    fireEvent.click(screen.getByRole("button", { name: /A1B2C3/ }));
    expect(screen.getAllByText("72%").length).toBeGreaterThan(0);

    act(() => useDashboardStore.getState().ingestMqttMessage({
      topic: "/telemetry/A1B2C3",
      payload: telemetryWithBattery(41),
      receivedAt: receivedAt + 100,
    }));
    expect(screen.getAllByText("41%").length).toBeGreaterThan(0);
  });
});

function telemetryWithBattery(stateOfCharge: number) {
  return {
    motor_telemetry: "Stopped" as const,
    battery_telemetry: {
      voltage: 7.6,
      current: 0.4,
      temperature: 28,
      is_charging: false,
      state_of_charge: stateOfCharge,
    },
    imu_telemetry: {
      timestamp_us: 123,
      raw: {
        accelerometer: [0, 0, 9.81] as [number, number, number],
        gyroscope: [0, 0, 0] as [number, number, number],
        magnetometer: [1, 0, 0] as [number, number, number],
        temperature: 26,
      },
      filtered: {
        accelerometer: [0, 0, 9.81] as [number, number, number],
        gyroscope: [0, 0, 0] as [number, number, number],
        magnetometer: [1, 0, 0] as [number, number, number],
        linear_acceleration: [0, 0, 0] as [number, number, number],
        quaternion: [0, 0, 0, 1] as [number, number, number, number],
        roll: 0,
        pitch: 0,
        heading: 0,
        is_stationary: true,
      },
    },
    network_telemetry: { rssi: -52, ip_address: "192.168.8.42" },
  };
}
