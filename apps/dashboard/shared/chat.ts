import { z } from "zod";
import { DEVICE_ID_PATTERN, FormationCommandSchema, type FormationCommand } from "./protocol.js";

/** Longest single turn. Generous for typing, and a bound on what reaches the model. */
export const CHAT_MESSAGE_MAX_LENGTH = 4000;

/** How much history travels with a request. Enough for "spread out more" to mean something. */
export const CHAT_HISTORY_MAX_TURNS = 20;

/** Largest recording the transcription endpoint accepts, in bytes. Minutes of Opus audio. */
export const CHAT_AUDIO_MAX_BYTES = 4 * 1024 * 1024;

export const CHAT_ROLES = ["user", "assistant"] as const;

export const ChatMessageSchema = z.object({
  role: z.enum(CHAT_ROLES),
  content: z.string().min(1).max(CHAT_MESSAGE_MAX_LENGTH),
});

export type ChatMessage = z.infer<typeof ChatMessageSchema>;

/**
 * What the browser sends.
 *
 * Only the conversation: the fleet's state is derived server-side from the gateway's own retained
 * snapshots, so the agent's view of the swarm cannot be shaped by the client and the chat panel
 * needs no state plumbing of its own.
 */
export const ChatRequestSchema = z.object({
  messages: z.array(ChatMessageSchema).min(1).max(CHAT_HISTORY_MAX_TURNS),
});

export type ChatRequest = z.infer<typeof ChatRequestSchema>;

export const ChatReplySchema = z.object({
  /** What to show in the transcript. Always present, even when no command was produced. */
  reply: z.string(),
  /** The command to publish, already validated and clamped, or null for a conversational turn. */
  command: FormationCommandSchema.nullable(),
  /** One line for the action chip, or null when there is no command. */
  commandSummary: z.string().nullable(),
  /**
   * Whether an operator must press Apply first.
   *
   * True for a geometry the agent invented: the 17 compiled-in programs are known quantities that
   * an operator can undo, whereas a novel shape deserves a look before it reaches the floor.
   */
  requiresConfirmation: z.boolean(),
});

export type ChatReply = z.infer<typeof ChatReplySchema>;

export const TranscriptReplySchema = z.object({
  text: z.string(),
});

export type TranscriptReply = z.infer<typeof TranscriptReplySchema>;

export const ChatStatusSchema = z.object({
  /** False when no API key is configured, so the panel can say so instead of failing on send. */
  enabled: z.boolean(),
  model: z.string().nullable(),
});

export type ChatStatus = z.infer<typeof ChatStatusSchema>;

/** What the agent is told about the swarm it is steering. */
export type FleetSnapshot = {
  /** Every robot the gateway has heard from, whether or not it has a position yet. */
  robotIds: string[];
  /** Those with a pose, which are the ones a formation can actually place. */
  posedRobotIds: string[];
  activeFormation: FormationCommand | null;
};

export function isDeviceIdLike(value: string): boolean {
  return DEVICE_ID_PATTERN.test(value);
}
