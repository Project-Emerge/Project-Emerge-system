import { z } from "zod";
import {
  ArucoMapSchema,
  FormationCommandSchema,
  NeighborsSchema,
  RobotConfigurationSchema,
  isDeviceId,
  type ArucoMap,
  type FormationCommand,
  type Neighbors,
  type RobotConfiguration,
} from "../../shared/protocol";

const finiteNumber = z.number().finite();
const Vec3Schema = z.tuple([finiteNumber, finiteNumber, finiteNumber]);
const QuaternionSchema = z.tuple([finiteNumber, finiteNumber, finiteNumber, finiteNumber]);
const CartesianVectorSchema = z.object({ x: finiteNumber, y: finiteNumber, z: finiteNumber });
const QuaternionObjectSchema = z.object({
  x: finiteNumber,
  y: finiteNumber,
  z: finiteNumber,
  w: finiteNumber,
});
const EulerDegreesSchema = z.object({ roll: finiteNumber, pitch: finiteNumber, yaw: finiteNumber });

export const PoseSchema = z.object({
  x_m: finiteNumber,
  y_m: finiteNumber,
  z_m: finiteNumber.optional(),
  roll_rad: finiteNumber.optional(),
  pitch_rad: finiteNumber.optional(),
  heading_rad: finiteNumber,
  speed_m_s: finiteNumber,
  // VisionSystem does not estimate covariance. Preserve the distinction
  // between an unavailable variance and a measured numeric value.
  position_variance_m2: finiteNumber.nonnegative().nullable().default(null),
  timestamp_us: z.number().int().nonnegative(),
  schema_version: z.literal(1).optional(),
  tag_id: z.number().int().nonnegative().optional(),
  frame_id: z.string().optional(),
  sequence: z.number().int().nonnegative().optional(),
  captured_at: z.string().optional(),
  published_at: z.string().optional(),
  position_m: CartesianVectorSchema.optional(),
  orientation_xyzw: QuaternionObjectSchema.optional(),
  velocity_m_s: CartesianVectorSchema.optional(),
  angular_velocity_rad_s: CartesianVectorSchema.optional(),
  euler_deg: EulerDegreesSchema.optional(),
  visible_by: z.array(z.string()).optional(),
  reprojection_error_px: finiteNumber.nonnegative().optional(),
  quality: finiteNumber.min(0).max(1).optional(),
  predicted: z.boolean().optional(),
  age_ms: finiteNumber.nonnegative().optional(),
  valid: z.boolean().optional(),
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
export type { RobotConfiguration, ArucoMap, Neighbors };

export type InboundMessage =
  | { kind: "pose"; deviceId: string; payload: Pose }
  | { kind: "telemetry"; deviceId: string; payload: Telemetry }
  | { kind: "imu"; deviceId: string; payload: ImuTelemetry }
  | { kind: "neighbors"; deviceId: string; payload: Neighbors }
  | { kind: "robot-config"; deviceId: string; payload: RobotConfiguration }
  | { kind: "aruco-map"; payload: ArucoMap }
  | { kind: "formation"; payload: FormationCommand };

function parseDeviceTopic(topic: string, prefix: "/pose/" | "/telemetry/" | "/imu/" | "/neighbors/"): string | null {
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

  const neighborsId = parseDeviceTopic(topic, "/neighbors/");
  if (neighborsId) {
    const parsed = NeighborsSchema.safeParse(payload);
    // The neighborhood service echoes the sender back in some modes; keep the list about the others.
    return parsed.success
      ? { kind: "neighbors", deviceId: neighborsId, payload: parsed.data.filter((id) => id !== neighborsId) }
      : null;
  }

  const robotConfigPrefix = "/config/robots/";
  if (topic.startsWith(robotConfigPrefix)) {
    const deviceId = topic.slice(robotConfigPrefix.length);
    const parsed = RobotConfigurationSchema.safeParse(payload);
    return isDeviceId(deviceId) && parsed.success
      ? { kind: "robot-config", deviceId, payload: parsed.data }
      : null;
  }

  if (topic === "/config/aruco-map") {
    const parsed = ArucoMapSchema.safeParse(payload);
    return parsed.success ? { kind: "aruco-map", payload: parsed.data } : null;
  }

  if (topic === "/config/formation") {
    const parsed = FormationCommandSchema.safeParse(payload);
    return parsed.success ? { kind: "formation", payload: parsed.data } : null;
  }

  return null;
}
