import type { FleetSnapshot } from "../shared/chat.js";
import { FormationCommandSchema, type GatewayMqttMessage } from "../shared/protocol.js";

const POSE_TOPIC = /^\/pose\/([A-F0-9]{6})$/;
const TELEMETRY_TOPIC = /^\/(?:telemetry|imu)\/([A-F0-9]{6})$/;

/**
 * What the chat agent is told about the swarm, read from the gateway's retained snapshot map.
 *
 * Derived server-side rather than sent up by the browser: the agent's view of which robots exist
 * then cannot be shaped by whatever is calling the endpoint, and the panel needs no state of its
 * own. It is the same map the gateway replays to every new WebSocket client, so it carries
 * exactly what the dashboard itself is showing.
 */
export function deriveFleetSnapshot(snapshots: Map<string, GatewayMqttMessage>): FleetSnapshot {
  const posed = new Set<string>();
  const known = new Set<string>();

  for (const topic of snapshots.keys()) {
    const pose = POSE_TOPIC.exec(topic);
    if (pose) {
      posed.add(pose[1]);
      known.add(pose[1]);
      continue;
    }
    // A robot reporting telemetry but no pose is real and worth naming: the agent should be able
    // to say a leader has no position rather than pretend the robot is not there.
    const telemetry = TELEMETRY_TOPIC.exec(topic);
    if (telemetry) known.add(telemetry[1]);
  }

  const retained = snapshots.get("/config/formation");
  const parsed = retained ? FormationCommandSchema.safeParse(retained.payload) : null;

  return {
    robotIds: [...known].sort(),
    posedRobotIds: [...posed].sort(),
    // A retained payload this gateway cannot parse is reported as no formation rather than
    // thrown: an unreadable topic must not take the whole chat down with it.
    activeFormation: parsed?.success ? parsed.data : null,
  };
}

/** The fleet state as a block of prompt text, kept together with what produces it. */
export function describeFleetForPrompt(fleet: FleetSnapshot): string {
  const lines: string[] = [];
  lines.push(
    fleet.posedRobotIds.length === 0
      ? "No robot has a known position yet, so no formation can be built around one."
      : `Robots with a known position (${fleet.posedRobotIds.length}): ${fleet.posedRobotIds.join(", ")}.`,
  );
  const unposed = fleet.robotIds.filter((id) => !fleet.posedRobotIds.includes(id));
  if (unposed.length > 0) {
    lines.push(`Heard from but not yet located: ${unposed.join(", ")}. Do not use these as a leader.`);
  }
  if (fleet.activeFormation) {
    const { program, anchor, leaderId, params, custom } = fleet.activeFormation;
    lines.push(
      `Currently running: ${program} (anchor ${anchor}${leaderId ? `, leader ${leaderId}` : ""}).`,
    );
    const tuned = Object.entries(params);
    if (tuned.length > 0) {
      lines.push(`Its parameters: ${tuned.map(([key, value]) => `${key}=${value}`).join(", ")}.`);
    }
    if (custom) {
      lines.push(`Its geometry: ${JSON.stringify(custom)}.`);
    }
  } else {
    lines.push("No formation is running yet.");
  }
  return lines.join("\n");
}
