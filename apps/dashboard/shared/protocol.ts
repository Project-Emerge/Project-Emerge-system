import { z } from "zod";

export const DEVICE_ID_PATTERN = /^[A-F0-9]{6}$/;

const finiteNumber = z.number().finite();
const normalizedMotorSpeed = finiteNumber.min(-1).max(1);

export const MotorCommandSchema = z.union([
  z.literal("Stop"),
  z.object({
    Move: z.object({
      left: normalizedMotorSpeed,
      right: normalizedMotorSpeed,
    }),
  }),
]);

export const RobotConfigurationSchema = z.object({
  motors: z.object({
    ema_filter_alpha: finiteNumber.min(0).max(1).nullable(),
    max_speed: finiteNumber.nonnegative(),
  }),
});

export const OtaConfigurationSchema = z.object({
  server: z.string().min(1).max(96).refine(
    (server) => !/[\s/#?]/.test(server),
    "OTA server must be a host or host:port without a URL path.",
  ),
});

export type RobotConfiguration = z.infer<typeof RobotConfigurationSchema>;
export type OtaConfiguration = z.infer<typeof OtaConfigurationSchema>;
export type MotorCommand = z.infer<typeof MotorCommandSchema>;

export const ClientPublishMessageSchema = z.object({
  type: z.literal("publish"),
  requestId: z.string().min(1).max(80),
  topic: z.string(),
  payload: z.unknown(),
});

export type ClientPublishMessage = z.infer<typeof ClientPublishMessageSchema>;

export type GatewayStatus = "connecting" | "connected" | "offline";

export type GatewayMqttMessage = {
  topic: string;
  payload: unknown;
  receivedAt: number;
};

export type GatewayServerMessage =
  | { type: "connection"; status: GatewayStatus }
  | { type: "snapshot"; messages: GatewayMqttMessage[] }
  | ({ type: "mqtt" } & GatewayMqttMessage)
  | { type: "publish-result"; requestId: string; ok: true }
  | { type: "publish-result"; requestId: string; ok: false; error: string };

export const MQTT_SUBSCRIPTIONS = [
  "/pose/+",
  "/telemetry/+",
  "/imu/+",
  "/config/ota",
  "/config/robots/+",
] as const;

export function isDeviceId(value: string): boolean {
  return DEVICE_ID_PATTERN.test(value);
}

export function robotConfigurationTopic(deviceId: string): string {
  if (!isDeviceId(deviceId)) {
    throw new Error("Invalid robot ID");
  }
  return `/config/robots/${deviceId}`;
}

export function otaCheckTopic(deviceId: string): string {
  if (!isDeviceId(deviceId)) {
    throw new Error("Invalid robot ID");
  }
  return `/ota/check/${deviceId}`;
}

export function motorCommandTopic(deviceId: string): string {
  if (!isDeviceId(deviceId)) {
    throw new Error("Invalid robot ID");
  }
  return `/motors/${deviceId}`;
}

export function otaConfigurationTopic(): "/config/ota" {
  return "/config/ota";
}

export function isOtaCheckTopic(topic: string): boolean {
  return /^\/ota\/check\/[A-F0-9]{6}$/.test(topic);
}

export function isMotorCommandTopic(topic: string): boolean {
  return /^\/motors\/[A-F0-9]{6}$/.test(topic);
}

export function isTransientCommandTopic(topic: string): boolean {
  return isOtaCheckTopic(topic) || isMotorCommandTopic(topic);
}

export function isAllowedConfigurationTopic(topic: string): boolean {
  return topic === "/config/ota" || /^\/config\/robots\/[A-F0-9]{6}$/.test(topic);
}

export function validateConfigurationPublication(topic: string, payload: unknown): string | null {
  if (topic === "/config/ota") {
    const result = OtaConfigurationSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid OTA configuration";
  }

  if (/^\/config\/robots\/[A-F0-9]{6}$/.test(topic)) {
    const result = RobotConfigurationSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid robot configuration";
  }

  return "Configuration topic is not allowed";
}

export function validateClientPublication(topic: string, payload: unknown): string | null {
  if (isOtaCheckTopic(topic)) return null;
  if (isMotorCommandTopic(topic)) {
    const result = MotorCommandSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid motor command";
  }
  return validateConfigurationPublication(topic, payload);
}
