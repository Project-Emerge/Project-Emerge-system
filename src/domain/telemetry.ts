import { z } from "zod";
import {
  AnchorsConfigurationSchema,
  EstimationConfigurationSchema,
  RobotConfigurationSchema,
  isDeviceId,
  type AnchorsConfiguration,
  type EstimationConfiguration,
  type RobotConfiguration,
} from "../../shared/protocol";

const finiteNumber = z.number().finite();
const Vec3Schema = z.tuple([finiteNumber, finiteNumber, finiteNumber]);
const QuaternionSchema = z.tuple([finiteNumber, finiteNumber, finiteNumber, finiteNumber]);

export const PoseSchema = z.object({
  x_m: finiteNumber,
  y_m: finiteNumber,
  heading_rad: finiteNumber,
  speed_m_s: finiteNumber,
  position_variance_m2: finiteNumber.nonnegative(),
  anchors_used: z.number().int().min(0).max(255),
  timestamp_us: z.number().int().nonnegative(),
});

const ImuRawSchema = z.object({
  accelerometer: Vec3Schema,
  gyroscope: Vec3Schema,
  magnetometer: Vec3Schema,
  temperature: finiteNumber.nullable(),
});

const ImuFilteredSchema = z.object({
  accelerometer: Vec3Schema,
  gyroscope: Vec3Schema,
  magnetometer: Vec3Schema,
  linear_acceleration: Vec3Schema,
  quaternion: QuaternionSchema,
  roll: finiteNumber,
  pitch: finiteNumber,
  heading: finiteNumber.nullable(),
  is_stationary: z.boolean(),
});

export const ImuTelemetrySchema = z.object({
  timestamp_us: z.number().int().nonnegative(),
  raw: ImuRawSchema,
  filtered: ImuFilteredSchema,
});

export const TelemetrySchema = z.object({
  motor_telemetry: z.union([
    z.literal("Stopped"),
    z.object({ Motoring: z.object({ left: finiteNumber, right: finiteNumber }) }),
  ]),
  battery_telemetry: z.object({
    voltage: finiteNumber,
    current: finiteNumber,
    temperature: finiteNumber,
    is_charging: z.boolean(),
    state_of_charge: z.number().int().min(0).max(100),
  }),
  imu_telemetry: ImuTelemetrySchema,
  network_telemetry: z.object({
    rssi: z.number().int(),
    ip_address: z.string().nullable(),
  }),
});

export type Pose = z.infer<typeof PoseSchema>;
export type ImuTelemetry = z.infer<typeof ImuTelemetrySchema>;
export type Telemetry = z.infer<typeof TelemetrySchema>;
export type { AnchorsConfiguration, EstimationConfiguration, RobotConfiguration };

export type InboundMessage =
  | { kind: "pose"; deviceId: string; payload: Pose }
  | { kind: "telemetry"; deviceId: string; payload: Telemetry }
  | { kind: "imu"; deviceId: string; payload: ImuTelemetry }
  | { kind: "anchors"; payload: AnchorsConfiguration }
  | { kind: "estimation"; payload: EstimationConfiguration }
  | { kind: "robot-config"; deviceId: string; payload: RobotConfiguration };

function parseDeviceTopic(topic: string, prefix: "/pose/" | "/telemetry/" | "/imu/"): string | null {
  if (!topic.startsWith(prefix)) {
    return null;
  }
  const deviceId = topic.slice(prefix.length);
  return isDeviceId(deviceId) ? deviceId : null;
}

export function parseInboundMqttMessage(topic: string, payload: unknown): InboundMessage | null {
  const poseId = parseDeviceTopic(topic, "/pose/");
  if (poseId) {
    const parsed = PoseSchema.safeParse(payload);
    return parsed.success ? { kind: "pose", deviceId: poseId, payload: parsed.data } : null;
  }

  const telemetryId = parseDeviceTopic(topic, "/telemetry/");
  if (telemetryId) {
    const parsed = TelemetrySchema.safeParse(payload);
    return parsed.success ? { kind: "telemetry", deviceId: telemetryId, payload: parsed.data } : null;
  }

  const imuId = parseDeviceTopic(topic, "/imu/");
  if (imuId) {
    const parsed = ImuTelemetrySchema.safeParse(payload);
    return parsed.success ? { kind: "imu", deviceId: imuId, payload: parsed.data } : null;
  }

  if (topic === "/config/anchors") {
    const parsed = AnchorsConfigurationSchema.safeParse(payload);
    return parsed.success ? { kind: "anchors", payload: parsed.data } : null;
  }

  if (topic === "/config/estimation") {
    const parsed = EstimationConfigurationSchema.safeParse(payload);
    return parsed.success ? { kind: "estimation", payload: parsed.data } : null;
  }

  const robotConfigPrefix = "/config/robots/";
  if (topic.startsWith(robotConfigPrefix)) {
    const deviceId = topic.slice(robotConfigPrefix.length);
    const parsed = RobotConfigurationSchema.safeParse(payload);
    return isDeviceId(deviceId) && parsed.success
      ? { kind: "robot-config", deviceId, payload: parsed.data }
      : null;
  }

  return null;
}
