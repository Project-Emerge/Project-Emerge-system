import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type { GatewayMqttMessage, GatewayStatus } from "../../shared/protocol";
import {
  parseInboundMqttMessage,
  type AnchorsConfiguration,
  type EstimationConfiguration,
  type ImuTelemetry,
  type Pose,
  type RobotConfiguration,
  type Telemetry,
} from "../domain/telemetry";

export type RobotLiveState = {
  id: string;
  pose?: Pose;
  telemetry?: Telemetry;
  imu?: ImuTelemetry;
  configuration?: RobotConfiguration;
  lastSeenAt: number;
  lastPoseAt?: number;
};

type DashboardState = {
  connectionStatus: GatewayStatus;
  robots: Record<string, RobotLiveState>;
  robotIds: string[];
  posedRobotIds: string[];
  anchorsConfiguration?: AnchorsConfiguration;
  estimationConfiguration?: EstimationConfiguration;
  selectedRobotId: string | null;
  lastProtocolError: string | null;
  setConnectionStatus: (status: GatewayStatus) => void;
  ingestMqttMessage: (message: GatewayMqttMessage) => void;
  toggleSelectedRobot: (id: string) => void;
  closeSidebar: () => void;
  clearProtocolError: () => void;
  resetForTests: () => void;
};

const initialData = {
  connectionStatus: "connecting" as GatewayStatus,
  robots: {} as Record<string, RobotLiveState>,
  robotIds: [] as string[],
  posedRobotIds: [] as string[],
  anchorsConfiguration: undefined,
  estimationConfiguration: undefined,
  selectedRobotId: null,
  lastProtocolError: null,
};

function robotWithUpdate(
  state: DashboardState,
  id: string,
  update: (robot: RobotLiveState) => RobotLiveState,
): Pick<DashboardState, "robots" | "robotIds"> {
  const exists = Boolean(state.robots[id]);
  const current = state.robots[id] ?? { id, lastSeenAt: 0 };
  return {
    robots: { ...state.robots, [id]: update(current) },
    robotIds: exists ? state.robotIds : [...state.robotIds, id].sort(),
  };
}

export const useDashboardStore = create<DashboardState>()(
  subscribeWithSelector((set) => ({
    ...initialData,
    setConnectionStatus: (connectionStatus) => set({ connectionStatus }),
    ingestMqttMessage: (message) => {
      const inbound = parseInboundMqttMessage(message.topic, message.payload);
      if (!inbound) {
        if (typeof message.payload === "string") {
          set({ lastProtocolError: `Invalid payload on ${message.topic}` });
        }
        return;
      }

      set((state) => {
        switch (inbound.kind) {
          case "anchors":
            return { anchorsConfiguration: inbound.payload, lastProtocolError: null };
          case "estimation":
            return { estimationConfiguration: inbound.payload, lastProtocolError: null };
          case "pose":
            return {
              ...robotWithUpdate(state, inbound.deviceId, (robot) => ({
                ...robot,
                pose: inbound.payload,
                lastSeenAt: message.receivedAt,
                lastPoseAt: message.receivedAt,
              })),
              posedRobotIds: state.posedRobotIds.includes(inbound.deviceId)
                ? state.posedRobotIds
                : [...state.posedRobotIds, inbound.deviceId].sort(),
              lastProtocolError: null,
            };
          case "telemetry":
            return {
              ...robotWithUpdate(state, inbound.deviceId, (robot) => ({
                ...robot,
                telemetry: inbound.payload,
                lastSeenAt: message.receivedAt,
              })),
              lastProtocolError: null,
            };
          case "imu":
            return {
              ...robotWithUpdate(state, inbound.deviceId, (robot) => ({
                ...robot,
                imu: inbound.payload,
                lastSeenAt: message.receivedAt,
              })),
              lastProtocolError: null,
            };
          case "robot-config":
            return {
              ...robotWithUpdate(state, inbound.deviceId, (robot) => ({
                ...robot,
                configuration: inbound.payload,
                lastSeenAt: message.receivedAt,
              })),
              lastProtocolError: null,
            };
        }
      });
    },
    toggleSelectedRobot: (id) => set((state) => ({ selectedRobotId: state.selectedRobotId === id ? null : id })),
    closeSidebar: () => set({ selectedRobotId: null }),
    clearProtocolError: () => set({ lastProtocolError: null }),
    resetForTests: () => set(initialData),
  })),
);

export function getRobotImu(robot?: RobotLiveState): ImuTelemetry | undefined {
  return robot?.imu ?? robot?.telemetry?.imu_telemetry;
}

export function isRobotStale(robot: RobotLiveState, now = Date.now()): boolean {
  return now - robot.lastSeenAt > 3_000;
}
