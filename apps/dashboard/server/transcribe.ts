import { HumanMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { ChatGoogleGenerativeAI } from "@langchain/google-genai";
import { DEFAULT_CHAT_MODEL } from "./chat-agent.js";

export type TranscriberOptions = {
  apiKey?: string;
  model?: string;
  /** Injected by the tests in place of a real Gemini client. */
  chatModel?: BaseChatModel;
};

export type Transcriber = {
  transcribe(audio: Buffer, mimeType: string): Promise<string>;
};

const INSTRUCTION = [
  "Transcribe this recording of a person speaking, verbatim.",
  "It is an instruction to a robot swarm, so it may name a formation or a robot id.",
  "Reply with the words only: no quotation marks, no commentary, no explanation.",
  "If there is no intelligible speech, reply with nothing at all.",
].join(" ");

/** Audio types a browser's MediaRecorder actually produces, mapped to what Gemini accepts. */
const SUPPORTED_PREFIXES = ["audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/aac"];

export function isSupportedAudioType(mimeType: string): boolean {
  const bare = mimeType.split(";")[0].trim().toLowerCase();
  return SUPPORTED_PREFIXES.includes(bare);
}

/**
 * Turns a recording into text with one Gemini call.
 *
 * Separate from the agent, and returning the transcript rather than acting on it, so the operator
 * sees what was heard and can correct it before it reaches the swarm. Mishearing "stop" as "spread
 * out" should cost an edit, not a fleet-wide manoeuvre.
 */
export function createTranscriber(options: TranscriberOptions): Transcriber {
  const model = options.chatModel ?? createGeminiModel(options);
  return {
    async transcribe(audio, mimeType) {
      // The bare type without codec parameters: Gemini rejects `audio/webm;codecs=opus`.
      const bare = mimeType.split(";")[0].trim().toLowerCase();
      const response = await model.invoke([
        new HumanMessage({
          content: [
            { type: "text", text: INSTRUCTION },
            { type: "media", mimeType: bare, data: audio.toString("base64") },
          ],
        }),
      ]);
      return flatten(response.content);
    },
  };
}

function flatten(content: unknown): string {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content
    .map((block) =>
      typeof block === "string"
        ? block
        : typeof block === "object" && block !== null && "text" in block
          ? String((block as { text: unknown }).text)
          : "",
    )
    .join("")
    .trim();
}

function createGeminiModel(options: TranscriberOptions): BaseChatModel {
  if (!options.apiKey) {
    throw new Error("A Gemini API key is required to transcribe audio.");
  }
  return new ChatGoogleGenerativeAI({
    model: options.model ?? DEFAULT_CHAT_MODEL,
    apiKey: options.apiKey,
    temperature: 0,
  }) as unknown as BaseChatModel;
}
