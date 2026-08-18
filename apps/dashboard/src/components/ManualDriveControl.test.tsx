import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ManualDriveControl, differentialDriveCommand } from "./ManualDriveControl";
import { useDashboardStore } from "../store/dashboard-store";

const gateway = vi.hoisted(() => ({ publish: vi.fn().mockResolvedValue(undefined) }));

vi.mock("../services/gateway-context", () => ({
  useGatewayClient: () => gateway,
}));

afterEach(() => {
  cleanup();
  gateway.publish.mockClear();
  useDashboardStore.getState().resetForTests();
  vi.useRealTimers();
});

describe("differential drive mixing", () => {
  it("maps forward, rotation, and the center dead zone to independent wheels", () => {
    expect(differentialDriveCommand({ x: 0.05, y: 0.05 })).toBe("Stop");
    expect(differentialDriveCommand({ x: 0, y: 1 })).toEqual({ Move: { left: 1, right: 1 } });
    expect(differentialDriveCommand({ x: 0, y: -1 })).toEqual({ Move: { left: -1, right: -1 } });
    expect(differentialDriveCommand({ x: 1, y: 0 })).toEqual({ Move: { left: 1, right: -1 } });
  });

  it("combines simultaneous forward and turning input without exceeding wheel limits", () => {
    const command = differentialDriveCommand({ x: 1, y: 1 });
    expect(command).not.toBe("Stop");
    if (command !== "Stop") {
      expect(command.Move.left).toBe(1);
      expect(command.Move.right).toBeGreaterThan(-1);
      expect(command.Move.right).toBeLessThan(command.Move.left);
    }
  });
});

describe("manual drive control", () => {
  it("publishes at 10 Hz while held and sends an immediate stop on release", () => {
    vi.useFakeTimers();
    const now = Date.now();
    useDashboardStore.setState({
      connectionStatus: "connected",
      selectedRobotId: "A1B2C3",
      robotIds: ["A1B2C3"],
      robots: { A1B2C3: { id: "A1B2C3", lastSeenAt: now } },
    });
    render(<ManualDriveControl now={now} />);
    const joystick = screen.getByLabelText("Drive joystick");
    Object.defineProperty(joystick, "getBoundingClientRect", {
      value: () => ({ left: 0, top: 0, width: 148, height: 148, right: 148, bottom: 148, x: 0, y: 0, toJSON: () => ({}) }),
    });

    fireEvent.pointerDown(joystick, { button: 0, pointerId: 7, clientX: 74, clientY: 10 });
    expect(gateway.publish).toHaveBeenNthCalledWith(1, "/motors/A1B2C3", { Move: { left: 1, right: 1 } });

    act(() => vi.advanceTimersByTime(300));
    expect(gateway.publish).toHaveBeenCalledTimes(4);

    fireEvent.pointerUp(joystick, { pointerId: 7, clientX: 74, clientY: 10 });
    expect(gateway.publish).toHaveBeenLastCalledWith("/motors/A1B2C3", "Stop");
    act(() => vi.advanceTimersByTime(300));
    expect(gateway.publish).toHaveBeenCalledTimes(5);
  });

  it("stops the previous robot instead of transferring motion when selection changes", () => {
    vi.useFakeTimers();
    const now = Date.now();
    useDashboardStore.setState({
      connectionStatus: "connected",
      selectedRobotId: "A1B2C3",
      robotIds: ["A1B2C3", "D4E5F6"],
      robots: {
        A1B2C3: { id: "A1B2C3", lastSeenAt: now },
        D4E5F6: { id: "D4E5F6", lastSeenAt: now },
      },
    });
    render(<ManualDriveControl now={now} />);
    const joystick = screen.getByLabelText("Drive joystick");
    Object.defineProperty(joystick, "getBoundingClientRect", {
      value: () => ({ left: 0, top: 0, width: 148, height: 148, right: 148, bottom: 148, x: 0, y: 0, toJSON: () => ({}) }),
    });
    fireEvent.pointerDown(joystick, { button: 0, pointerId: 7, clientX: 74, clientY: 10 });

    act(() => useDashboardStore.getState().toggleSelectedRobot("D4E5F6"));
    expect(gateway.publish).toHaveBeenLastCalledWith("/motors/A1B2C3", "Stop");
    expect(gateway.publish).not.toHaveBeenCalledWith("/motors/D4E5F6", expect.objectContaining({ Move: expect.anything() }));
    act(() => vi.advanceTimersByTime(300));
    expect(gateway.publish).toHaveBeenCalledTimes(2);
  });

  it("preserves the joystick and combines both controllers in dual mode", () => {
    const now = Date.now();
    useDashboardStore.setState({
      connectionStatus: "connected",
      selectedRobotId: "A1B2C3",
      robotIds: ["A1B2C3"],
      robots: { A1B2C3: { id: "A1B2C3", lastSeenAt: now } },
    });
    render(<ManualDriveControl now={now} />);
    const joystick = screen.getByLabelText("Drive joystick");
    Object.defineProperty(joystick, "getBoundingClientRect", {
      value: () => ({ left: 0, top: 0, width: 148, height: 148, right: 148, bottom: 148, x: 0, y: 0, toJSON: () => ({}) }),
    });
    const joystickMode = screen.getByRole("button", { name: "Joystick" });
    const dualMode = screen.getByRole("button", { name: "Dual control" });
    expect(joystickMode).toHaveAttribute("aria-pressed", "true");
    expect(dualMode).toHaveAttribute("aria-pressed", "false");

    fireEvent.pointerDown(joystick, { button: 0, pointerId: 7, clientX: 74, clientY: 10 });
    fireEvent.click(dualMode);
    expect(gateway.publish).toHaveBeenLastCalledWith("/motors/A1B2C3", "Stop");
    expect(dualMode).toHaveAttribute("aria-pressed", "true");

    gateway.publish.mockClear();
    const throttle = screen.getByLabelText("Forward reverse controller");
    const turn = screen.getByLabelText("Turn controller");
    for (const controller of [throttle, turn]) {
      Object.defineProperty(controller, "getBoundingClientRect", {
        value: () => ({ left: 0, top: 0, width: 112, height: 112, right: 112, bottom: 112, x: 0, y: 0, toJSON: () => ({}) }),
      });
    }

    fireEvent.pointerDown(throttle, { button: 0, pointerId: 8, clientX: 56, clientY: 0 });
    expect(gateway.publish).toHaveBeenLastCalledWith("/motors/A1B2C3", { Move: { left: 1, right: 1 } });
    fireEvent.pointerDown(turn, { button: 0, pointerId: 9, clientX: 112, clientY: 56 });
    expect(gateway.publish).toHaveBeenLastCalledWith("/motors/A1B2C3", differentialDriveCommand({ x: 1, y: 1 }));

    fireEvent.pointerUp(throttle, { pointerId: 8, clientX: 56, clientY: 0 });
    expect(gateway.publish).toHaveBeenLastCalledWith("/motors/A1B2C3", { Move: { left: 1, right: -1 } });
    fireEvent.pointerUp(turn, { pointerId: 9, clientX: 112, clientY: 56 });
    expect(gateway.publish).toHaveBeenLastCalledWith("/motors/A1B2C3", "Stop");
  });
});
