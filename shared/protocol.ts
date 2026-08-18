import { z } from "zod";

export const DEVICE_ID_PATTERN = /^[A-F0-9]{6}$/;
export const ANCHOR_IDS = [0xa001, 0xa002, 0xa003, 0xa004] as const;

const finiteNumber = z.number().finite();

export const AnchorCalibrationSchema = z.object({
  anchor_id: z.union(ANCHOR_IDS.map((id) => z.literal(id)) as [z.ZodLiteral<number>, ...z.ZodLiteral<number>[]]),
  x: finiteNumber,
  y: finiteNumber,
  z: finiteNumber,
  offset_mm: z.number().int(),
  scale_ppm: z.number().int(),
});

export const AnchorsConfigurationSchema = z.object({
  robot_antenna_height_m: finiteNumber.nonnegative(),
  anchors: z.array(AnchorCalibrationSchema).length(4),
}).superRefine((configuration, context) => {
  const present = new Set(configuration.anchors.map((anchor) => anchor.anchor_id));
  if (present.size !== ANCHOR_IDS.length || ANCHOR_IDS.some((anchorId) => !present.has(anchorId))) {
    context.addIssue({ code: "custom", message: "The configuration must contain each of the four anchors exactly once." });
  }
});

export const RobotConfigurationSchema = z.object({
  motors: z.object({
    ema_filter_alpha: finiteNumber.min(0).max(1).nullable(),
    max_speed: finiteNumber.nonnegative(),
  }),
});

export const EstimationConfigurationSchema = z.object({
  fusion_enabled: z.boolean(),
});

export const OtaConfigurationSchema = z.object({
  server: z.string().min(1).max(96).refine(
    (server) => !/[\s/#?]/.test(server),
    "OTA server must be a host or host:port without a URL path.",
  ),
});

export type AnchorCalibration = z.infer<typeof AnchorCalibrationSchema>;
export type AnchorsConfiguration = z.infer<typeof AnchorsConfigurationSchema>;
export type RobotConfiguration = z.infer<typeof RobotConfigurationSchema>;
export type EstimationConfiguration = z.infer<typeof EstimationConfigurationSchema>;
export type OtaConfiguration = z.infer<typeof OtaConfigurationSchema>;

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
  "/config/anchors",
  "/config/estimation",
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

export function otaConfigurationTopic(): "/config/ota" {
  return "/config/ota";
}

export function isOtaCheckTopic(topic: string): boolean {
  return /^\/ota\/check\/[A-F0-9]{6}$/.test(topic);
}

export function isAllowedConfigurationTopic(topic: string): boolean {
  return topic === "/config/anchors" || topic === "/config/estimation" || topic === "/config/ota" || /^\/config\/robots\/[A-F0-9]{6}$/.test(topic);
}

export function validateConfigurationPublication(topic: string, payload: unknown): string | null {
  if (topic === "/config/anchors") {
    const result = AnchorsConfigurationSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid anchor configuration";
  }

  if (topic === "/config/estimation") {
    const result = EstimationConfigurationSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid estimation configuration";
  }

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
