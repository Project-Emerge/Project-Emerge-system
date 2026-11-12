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

export const MotorConfigurationSchema = z.object({
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

export const ARUCO_MARKER_ID_MIN = 0;
export const ARUCO_MARKER_ID_MAX = 49; // cv2.aruco.DICT_4X4_50

// Matches canonical (no leading-zero) stringified integers 0-49.
const ARUCO_MARKER_KEY_PATTERN = /^([0-9]|[1-4][0-9])$/;

export const ArucoMapSchema = z
  .record(
    z.string().regex(ARUCO_MARKER_KEY_PATTERN, "Marker ID must be an integer between 0 and 49."),
    z.string().regex(DEVICE_ID_PATTERN, "Robot ID must be 6 uppercase hex characters."),
  )
  .refine(
    (map) => new Set(Object.values(map)).size === Object.values(map).length,
    { message: "Each robot can only be mapped to one ArUco marker." },
  );

export const NeighborsSchema = z.array(
  z.string().regex(DEVICE_ID_PATTERN, "Robot ID must be 6 uppercase hex characters."),
);

export const FORMATION_PROGRAMS = [
  "pointToLeader",
  "vShape",
  "lineShape",
  "circleShape",
  "squareShape",
  "verticalLineShape",
  "heartShape",
  "orbitCircle",
  "breathingCircle",
  "ringWave",
  "sineLine",
  // Scala carried on this same message and compiled by the runtime on arrival.
  "custom",
  "stop",
] as const;

export type FormationProgram = (typeof FORMATION_PROGRAMS)[number];

/**
 * How the robot a formation is built around is chosen.
 * - `leader` uses the operator's `leaderId`.
 * - `auto` lets the fleet elect one itself (sparse choice).
 */
export const FORMATION_ANCHORS = ["leader", "auto"] as const;

export type FormationAnchor = (typeof FORMATION_ANCHORS)[number];

/** Mirrors `FormationCompiler.MaxSourceLength` in the aggregate runtime. */
export const CUSTOM_SOURCE_MAX_LENGTH = 8_000;

/**
 * What a designed source must evaluate to.
 * - `formation`: a `Formation`, geometry only; the runtime plans, assigns and steers around it.
 * - `program`: a `BaseDemo`, a whole aggregate program run in place of the formation planner.
 */
export const CUSTOM_KINDS = ["formation", "program"] as const;

export type CustomKind = (typeof CUSTOM_KINDS)[number];

const customSource = z.string().min(1).max(CUSTOM_SOURCE_MAX_LENGTH);

/** A name for the design, shown on the chat's action chip and the arena toolbar. */
const customLabel = z.string().min(1).max(60).optional();

/**
 * A formation written as Scala and compiled by the aggregate runtime when it arrives, so one the
 * runtime has never seen costs no rebuild and no restart.
 *
 * The runtime compiles it in a sandbox -- only arithmetic, immutable collections and the formation
 * DSL are reachable, and every call is metered -- and falls back to a ring when it does not
 * compile or misbehaves. Check it first on `/formation/compile` to see why before publishing.
 */
export const CustomFormationSpecSchema = z.object({
  kind: z.enum(CUSTOM_KINDS),
  source: customSource,
  label: customLabel,
});

export type CustomFormationSpec = z.infer<typeof CustomFormationSpecSchema>;

export const FormationCommandSchema = z.object({
  program: z.enum(FORMATION_PROGRAMS),
  leaderId: z.string().regex(DEVICE_ID_PATTERN, "Robot ID must be 6 uppercase hex characters.").nullable(),
  // Optional with a default, so retained commands published before anchors existed still parse.
  anchor: z.enum(FORMATION_ANCHORS).default("leader"),
  params: z.record(z.string(), finiteNumber),
  // Optional, so a retained command published before custom formations existed still parses.
  custom: CustomFormationSpecSchema.nullish(),
}).superRefine((command, context) => {
  if (command.program === "custom" && !command.custom) {
    context.addIssue({
      code: "custom",
      path: ["custom"],
      message: "A custom formation must carry the code it is built from.",
    });
  }
});

export type MotorConfiguration = z.infer<typeof MotorConfigurationSchema>;
export type OtaConfiguration = z.infer<typeof OtaConfigurationSchema>;
export type MotorCommand = z.infer<typeof MotorCommandSchema>;
export type ArucoMap = z.infer<typeof ArucoMapSchema>;
export type Neighbors = z.infer<typeof NeighborsSchema>;
export type FormationCommand = z.infer<typeof FormationCommandSchema>;

/** Not retained: a request to compile a design, answered on `/formation/compile/<id>`. */
export const FORMATION_COMPILE_REQUEST_TOPIC = "/formation/compile";

/** Nothing an MQTT topic level could misread: mirrors the runtime's own check. */
const COMPILE_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export function formationCompileResultTopic(id: string): string {
  return `${FORMATION_COMPILE_REQUEST_TOPIC}/${id}`;
}

export function isFormationCompileResultTopic(topic: string): boolean {
  return topic.startsWith(`${FORMATION_COMPILE_REQUEST_TOPIC}/`)
    && COMPILE_ID_PATTERN.test(topic.slice(FORMATION_COMPILE_REQUEST_TOPIC.length + 1));
}

/**
 * `slots` is how many robots the design is laid out for, the anchor excluded, which is what
 * `ctx.count` will be on the floor; `params` are the live parameters over the runtime's defaults.
 */
export const FormationCompileRequestSchema = z.object({
  id: z.string().regex(COMPILE_ID_PATTERN),
  kind: z.enum(CUSTOM_KINDS),
  source: customSource,
  slots: z.number().int().min(1).max(64),
  params: z.record(z.string(), finiteNumber),
});

/** `line` and `column` are 1-based in the author's source, or 0 when a problem has no position. */
export const FormationCompileProblemSchema = z.object({
  line: z.number().int().nonnegative(),
  column: z.number().int().nonnegative(),
  message: z.string(),
});

/** `preview` is the geometry's slots at rest, in metres from the anchor; null for a program. */
export const FormationCompileResultSchema = z.object({
  id: z.string(),
  ok: z.boolean(),
  problems: z.array(FormationCompileProblemSchema),
  preview: z.array(z.tuple([finiteNumber, finiteNumber])).nullable(),
});

export type FormationCompileRequest = z.infer<typeof FormationCompileRequestSchema>;
export type FormationCompileProblem = z.infer<typeof FormationCompileProblemSchema>;
export type FormationCompileResult = z.infer<typeof FormationCompileResultSchema>;

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
  "/neighbors/+",
  "/config/ota",
  "/config/motors",
  "/config/aruco-map",
  "/config/formation",
] as const;

export function isDeviceId(value: string): boolean {
  return DEVICE_ID_PATTERN.test(value);
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

export function neighborsTopic(deviceId: string): string {
  if (!isDeviceId(deviceId)) {
    throw new Error("Invalid robot ID");
  }
  return `/neighbors/${deviceId}`;
}

export function otaConfigurationTopic(): "/config/ota" {
  return "/config/ota";
}

export function motorConfigurationTopic(): "/config/motors" {
  return "/config/motors";
}

export function arucoMapTopic(): "/config/aruco-map" {
  return "/config/aruco-map";
}

export function formationTopic(): "/config/formation" {
  return "/config/formation";
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
  return topic === "/config/ota"
    || topic === "/config/motors"
    || topic === "/config/aruco-map"
    || topic === "/config/formation";
}

export function validateConfigurationPublication(topic: string, payload: unknown): string | null {
  if (topic === "/config/ota") {
    const result = OtaConfigurationSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid OTA configuration";
  }

  if (topic === "/config/aruco-map") {
    const result = ArucoMapSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid ArUco marker mapping";
  }

  if (topic === "/config/formation") {
    const result = FormationCommandSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid formation command";
  }

  if (topic === "/config/motors") {
    const result = MotorConfigurationSchema.safeParse(payload);
    return result.success ? null : result.error.issues[0]?.message ?? "Invalid motor configuration";
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
