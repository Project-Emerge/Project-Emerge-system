import { useEffect, useRef, useState } from "react";
import { formationTopic, type FormationCommand } from "../../shared/protocol";
import { getFormationLabel, summariseCommand } from "../../shared/formations";
import type { ChatMessage, ChatStatus } from "../../shared/chat";
import { fetchChatStatus, sendChatMessage, transcribeAudio } from "../services/chat-client";
import {
  isVoiceSupported,
  MicrophoneDeniedError,
  startRecording,
  type Recording,
} from "../services/voice-recorder";
import { useGatewayClient } from "../services/gateway-context";
import { useDashboardStore } from "../store/dashboard-store";

/**
 * What an assistant turn offers the operator.
 *
 * `applied` covers the seventeen compiled-in programs, which go out immediately and keep an undo
 * within reach; `proposed` covers a geometry the agent invented, which waits. The two differ
 * because a built-in is a known quantity and a novel shape is not.
 */
type EntryAction =
  | {
    kind: "applied";
    command: FormationCommand;
    summary: string;
    previous: FormationCommand | null;
    reverted: boolean;
  }
  | { kind: "proposed"; command: FormationCommand; summary: string }
  | { kind: "settled"; summary: string; note: string };

type Entry = {
  id: number;
  role: "user" | "assistant";
  content: string;
  action?: EntryAction;
};

type Phase = "idle" | "thinking" | "recording" | "transcribing";

/** Undoing when there was no previous formation halts the fleet, which is the safe direction. */
const HALT: FormationCommand = {
  program: "stop",
  leaderId: null,
  anchor: "leader",
  params: {},
  custom: null,
};

export function ChatPanel({ onClose }: { onClose: () => void }): React.JSX.Element {
  const gateway = useGatewayClient();
  const connectionStatus = useDashboardStore((state) => state.connectionStatus);

  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const recording = useRef<Recording | null>(null);
  const nextId = useRef(0);
  const transcript = useRef<HTMLDivElement | null>(null);
  const voiceSupported = isVoiceSupported();

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    fetchChatStatus()
      .then((value) => { if (!cancelled) setStatus(value); })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setStatus({ enabled: false, model: null });
          setError(cause instanceof Error ? cause.message : "The swarm chat is unavailable.");
        }
      });
    return () => { cancelled = true; };
  }, []);

  // Release the microphone if the panel closes mid-recording, so the browser's indicator does
  // not stay on after the operator has moved away.
  useEffect(() => () => recording.current?.cancel(), []);

  useEffect(() => {
    // `scrollTop`, not `scrollTo`: the latter is unimplemented in jsdom, and assigning the
    // property is exactly as correct in a browser.
    const element = transcript.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [entries, phase]);

  function append(entry: Omit<Entry, "id">): number {
    const id = nextId.current++;
    setEntries((current) => [...current, { ...entry, id }]);
    return id;
  }

  function setAction(id: number, action: EntryAction): void {
    setEntries((current) => current.map((entry) => (entry.id === id ? { ...entry, action } : entry)));
  }

  async function publish(command: FormationCommand): Promise<void> {
    await gateway.publish(formationTopic(), command);
  }

  async function send(text: string): Promise<void> {
    const content = text.trim();
    if (!content) return;
    setError(null);
    setDraft("");
    append({ role: "user", content });
    // Built from the entries plus this turn, rather than from state, which has not settled yet.
    const history: ChatMessage[] = [
      ...entries.map((entry) => ({ role: entry.role, content: entry.content })),
      { role: "user" as const, content },
    ];
    setPhase("thinking");
    try {
      const reply = await sendChatMessage(history);
      const entryId = append({ role: "assistant", content: reply.reply });
      if (!reply.command) return;
      const summary = reply.commandSummary ?? summariseCommand(reply.command);
      if (reply.requiresConfirmation) {
        setAction(entryId, { kind: "proposed", command: reply.command, summary });
        return;
      }
      // Read the live store rather than the render-time value, so the undo target is whatever
      // was actually running the instant before this publish.
      const previous = useDashboardStore.getState().formation;
      try {
        await publish(reply.command);
        setAction(entryId, {
          kind: "applied",
          command: reply.command,
          summary,
          previous,
          reverted: false,
        });
      } catch (cause) {
        setAction(entryId, {
          kind: "settled",
          summary,
          note: cause instanceof Error ? `Not applied: ${cause.message}` : "Not applied.",
        });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The swarm chat could not answer.");
    } finally {
      setPhase("idle");
    }
  }

  async function apply(id: number, action: Extract<EntryAction, { kind: "proposed" }>): Promise<void> {
    setError(null);
    const previous = useDashboardStore.getState().formation;
    try {
      await publish(action.command);
      setAction(id, {
        kind: "applied",
        command: action.command,
        summary: action.summary,
        previous,
        reverted: false,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Apply failed.");
    }
  }

  async function undo(id: number, action: Extract<EntryAction, { kind: "applied" }>): Promise<void> {
    setError(null);
    try {
      await publish(action.previous ?? HALT);
      setAction(id, { ...action, reverted: true });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Undo failed.");
    }
  }

  async function toggleRecording(): Promise<void> {
    if (phase === "recording") {
      const session = recording.current;
      recording.current = null;
      if (!session) {
        setPhase("idle");
        return;
      }
      setPhase("transcribing");
      try {
        const audio = await session.stop();
        const text = await transcribeAudio(audio);
        // Into the composer rather than straight to the agent: mishearing "stop" as "spread out"
        // should cost an edit, not a fleet-wide manoeuvre.
        if (text) setDraft(text);
        else setError("I did not catch that. Try again, or type it.");
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "That recording could not be used.");
      } finally {
        setPhase("idle");
      }
      return;
    }

    setError(null);
    try {
      recording.current = await startRecording();
      setPhase("recording");
    } catch (cause) {
      setError(
        cause instanceof MicrophoneDeniedError
          ? "Microphone access was refused. Allow it in the browser to speak to the swarm."
          : cause instanceof Error ? cause.message : "No microphone is available.",
      );
    }
  }

  const offline = connectionStatus !== "connected";
  const disabledReason = status && !status.enabled
    ? "Set GEMINI_API_KEY in the root .env to use the swarm chat."
    : offline
      ? "The gateway is offline, so nothing can reach the swarm."
      : null;
  const busy = phase !== "idle";
  const canSend = !disabledReason && !busy && draft.trim().length > 0;

  return (
    <aside className="chat-dock" aria-label="Swarm chat">
      <div className="chat-header">
        <div>
          <span className="eyebrow">Fleet</span>
          <h2>Swarm chat</h2>
        </div>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close swarm chat">✕</button>
      </div>

      <div className="chat-transcript" ref={transcript} role="log" aria-live="polite">
        {entries.length === 0 && !disabledReason && (
          <p className="chat-empty">
            Ask for a formation — “ring everyone around D4E5F6”, “spread out more”, “draw a
            five-pointed star”. Speak it with the microphone if you prefer.
          </p>
        )}
        {entries.map((entry) => (
          <div className={`chat-bubble ${entry.role}`} key={entry.id}>
            <p>{entry.content}</p>
            {entry.action?.kind === "applied" && (
              <div className="chat-action-chip">
                <span>
                  {entry.action.reverted
                    ? `Reverted to ${entry.action.previous ? getFormationLabel(entry.action.previous.program) : "Stop"}`
                    : `Applied ${entry.action.summary}`}
                </span>
                {!entry.action.reverted && (
                  <button
                    type="button"
                    onClick={() => void undo(entry.id, entry.action as Extract<EntryAction, { kind: "applied" }>)}
                  >
                    Undo
                  </button>
                )}
              </div>
            )}
            {entry.action?.kind === "proposed" && (
              <div className="chat-action-chip proposed">
                <span>{entry.action.summary}</span>
                <button
                  type="button"
                  onClick={() => void apply(entry.id, entry.action as Extract<EntryAction, { kind: "proposed" }>)}
                  disabled={Boolean(disabledReason)}
                >
                  Apply
                </button>
                <button
                  type="button"
                  onClick={() => setAction(entry.id, {
                    kind: "settled",
                    summary: entry.action!.summary,
                    note: "Discarded",
                  })}
                >
                  Discard
                </button>
              </div>
            )}
            {entry.action?.kind === "settled" && (
              <div className="chat-action-chip">
                <span>{`${entry.action.note}: ${entry.action.summary}`}</span>
              </div>
            )}
          </div>
        ))}
        {phase === "thinking" && <p className="chat-thinking">Thinking…</p>}
        {phase === "transcribing" && <p className="chat-thinking">Transcribing…</p>}
      </div>

      {disabledReason && <p className="form-message error">{disabledReason}</p>}
      {error && <p className="form-message error">{error}</p>}

      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        {voiceSupported && (
          <button
            type="button"
            className={`chat-mic ${phase === "recording" ? "recording" : ""}`}
            aria-label={phase === "recording" ? "Stop recording" : "Speak to the swarm"}
            aria-pressed={phase === "recording"}
            disabled={Boolean(disabledReason) || phase === "thinking" || phase === "transcribing"}
            onClick={() => void toggleRecording()}
          >
            {phase === "recording" ? "◼" : "🎤"}
          </button>
        )}
        <input
          type="text"
          aria-label="Message the swarm"
          placeholder={phase === "recording" ? "Listening…" : "Ask for a formation…"}
          value={draft}
          disabled={Boolean(disabledReason) || busy}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="primary-button" disabled={!canSend} aria-label="Send">
          ▸
        </button>
      </form>
    </aside>
  );
}
