import {
  ChatReplySchema,
  ChatStatusSchema,
  TranscriptReplySchema,
  type ChatMessage,
  type ChatReply,
  type ChatStatus,
} from "../../shared/chat";

/** Pulls the gateway's `{ error }` sentence out of a failed response, the way the OTA upload does. */
async function failureOf(response: Response, fallback: string): Promise<Error> {
  const body = await response.json().catch(() => null) as { error?: string } | null;
  return new Error(body?.error ?? fallback);
}

/**
 * Whether the gateway has a Gemini key at all.
 *
 * Asked once when the panel opens so it can explain itself up front, rather than accepting a
 * message and then failing on send.
 */
export async function fetchChatStatus(): Promise<ChatStatus> {
  const response = await fetch("/api/chat/status");
  if (!response.ok) throw await failureOf(response, "The swarm chat is unavailable.");
  const parsed = ChatStatusSchema.safeParse(await response.json());
  if (!parsed.success) throw new Error("The gateway sent an unreadable chat status.");
  return parsed.data;
}

export async function sendChatMessage(messages: ChatMessage[]): Promise<ChatReply> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!response.ok) throw await failureOf(response, "The swarm chat could not answer.");
  const parsed = ChatReplySchema.safeParse(await response.json());
  // Validated on arrival as well as on departure: a command that reaches the swarm should never
  // depend on the gateway and the browser having agreed by convention alone.
  if (!parsed.success) throw new Error("The gateway sent an unreadable reply.");
  return parsed.data;
}

export async function transcribeAudio(audio: Blob): Promise<string> {
  const response = await fetch("/api/chat/transcribe", {
    method: "POST",
    headers: { "Content-Type": audio.type || "audio/webm" },
    body: audio,
  });
  if (!response.ok) throw await failureOf(response, "That recording could not be transcribed.");
  const parsed = TranscriptReplySchema.safeParse(await response.json());
  if (!parsed.success) throw new Error("The gateway sent an unreadable transcript.");
  return parsed.data.text;
}
