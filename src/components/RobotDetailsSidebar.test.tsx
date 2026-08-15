import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RobotDetailsSidebar } from "./RobotDetailsSidebar";
import { useDashboardStore } from "../store/dashboard-store";

afterEach(() => useDashboardStore.getState().resetForTests());

describe("sidebar robot", () => {
  it("si chiude con il relativo pulsante", () => {
    useDashboardStore.setState({
      selectedRobotId: "A1B2C3",
      robots: {
        A1B2C3: {
          id: "A1B2C3",
          lastSeenAt: Date.now(),
          pose: { x_m: 1, y_m: 2, heading_rad: 0, speed_m_s: 0, position_variance_m2: 0.01, anchors_used: 4, timestamp_us: 1 },
        },
      },
      robotIds: ["A1B2C3"],
      posedRobotIds: ["A1B2C3"],
    });
    render(<RobotDetailsSidebar />);
    expect(screen.getByRole("heading", { name: "A1B2C3" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(useDashboardStore.getState().selectedRobotId).toBeNull();
  });
});
