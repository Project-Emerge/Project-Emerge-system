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
import { useLocale } from "../services/locale-context";

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
  const { t } = useLocale();
  const gateway = useGatewayClient();
  const connectionStatus = useDashboardStore((state) => state.connectionStatus);

  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const recording = useRef<Recording | null>(null);
  const nextId = useRef(0);
  const transcript = useRef<HTMLDivElement | null>(null);
  const entriesRef = useRef(entries);
  entriesRef.current = entries;
  const voiceSupported = isVoiceSupported();

  useEffect(() => {
    if (phase !== "recording") {
      setRecordingSeconds(0);
      return;
    }
    const timer = setInterval(() => {
      setRecordingSeconds((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [phase]);

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
          setError(cause instanceof Error ? cause.message : t.chat.chatUnavailable);
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
      ...entriesRef.current.map((entry) => ({ role: entry.role, content: entry.content })),
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
          note: cause instanceof Error ? `${t.chat.applyFailed}: ${cause.message}` : t.chat.applyFailed,
        });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t.chat.chatCouldNotAnswer);
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
      setError(cause instanceof Error ? cause.message : t.chat.applyFailed);
    }
  }

  async function undo(id: number, action: Extract<EntryAction, { kind: "applied" }>): Promise<void> {
    setError(null);
    try {
      await publish(action.previous ?? HALT);
      setAction(id, { ...action, reverted: true });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t.chat.undoFailed);
    }
  }

  async function finishRecording(session: Recording): Promise<void> {
    if (recording.current !== session) return;
    recording.current = null;
    setPhase("transcribing");
    let text: string | null = null;
    try {
      const audio = await session.stop();
      text = await transcribeAudio(audio);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t.chat.recordingFailed);
      setPhase("idle");
      return;
    }

    if (text && text.trim()) {
      await send(text.trim());
    } else {
      setError(t.chat.didNotCatch);
      setPhase("idle");
    }
  }

  async function toggleRecording(): Promise<void> {
    if (phase === "recording") {
      const session = recording.current;
      if (session) {
        await finishRecording(session);
      } else {
        setPhase("idle");
      }
      return;
    }

    setError(null);
    try {
      let session: Recording | null = null;
      session = await startRecording({
        onAutoStop: () => {
          if (session) {
            void finishRecording(session);
          }
        },
      });
      recording.current = session;
      setPhase("recording");
    } catch (cause) {
      setError(
        cause instanceof MicrophoneDeniedError
          ? t.chat.micDenied
          : cause instanceof Error ? cause.message : t.chat.micUnavailable,
      );
    }
  }

  const offline = connectionStatus !== "connected";
  const disabledReason = status && !status.enabled
    ? t.chat.geminiKeyMissing
    : offline
      ? t.chat.gatewayOffline
      : null;
  const busy = phase !== "idle";
  const canSend = !disabledReason && !busy && draft.trim().length > 0;

  return (
    <aside className="chat-dock" aria-label={t.chat.swarmChat}>
      <div className="chat-header">
        <div>
          <span className="eyebrow">{t.chat.fleet}</span>
          <h2>{t.chat.swarmChat}</h2>
        </div>
        <button type="button" className="modal-close" onClick={onClose} aria-label={t.chat.closeAria}>✕</button>
      </div>

      <div className="chat-transcript" ref={transcript} role="log" aria-live="polite">
        {entries.length === 0 && !disabledReason && (
          <p className="chat-empty">
            {t.chat.emptyPrompt}
          </p>
        )}
        {entries.map((entry) => (
          <div className={`chat-bubble ${entry.role}`} key={entry.id}>
            <p>{entry.content}</p>
            {entry.action?.kind === "applied" && (
              <div className="chat-action-chip">
                <span>
                  {entry.action.reverted
                    ? t.chat.revertedTo(
                        entry.action.previous
                          ? (t.formationModal.programs[entry.action.previous.program]?.label ?? getFormationLabel(entry.action.previous.program))
                          : (t.formationModal.programs["stop"]?.label ?? "Stop")
                      )
                    : t.chat.appliedSummary(entry.action.summary)}
                </span>
                {!entry.action.reverted && (
                  <button
                    type="button"
                    onClick={() => void undo(entry.id, entry.action as Extract<EntryAction, { kind: "applied" }>)}
                  >
                    {t.chat.undoButton}
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
                  {t.chat.applyButton}
                </button>
                <button
                  type="button"
                  onClick={() => setAction(entry.id, {
                    kind: "settled",
                    summary: entry.action!.summary,
                    note: t.chat.discardedNote,
                  })}
                >
                  {t.chat.discardButton}
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
        {phase === "thinking" && <p className="chat-thinking">{t.chat.thinking}</p>}
        {phase === "transcribing" && <p className="chat-thinking">{t.chat.transcribing}</p>}
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
            aria-label={phase === "recording" ? t.chat.stopRecordingAria : t.chat.speakAria}
            aria-pressed={phase === "recording"}
            title={phase === "recording" ? t.chat.recordingTitle(recordingSeconds) : t.chat.speakTitle}
            disabled={Boolean(disabledReason) || phase === "thinking" || phase === "transcribing"}
            onClick={() => void toggleRecording()}
          >
            {phase === "recording" ? "◼" : "🎤"}
          </button>
        )}
        <input
          type="text"
          aria-label={t.chat.inputAria}
          placeholder={
            phase === "recording"
              ? t.chat.listeningPlaceholder(recordingSeconds)
              : t.chat.askPlaceholder
          }
          value={draft}
          disabled={Boolean(disabledReason) || busy}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="primary-button" disabled={!canSend} aria-label={t.chat.sendAria}>
          ▸
        </button>
      </form>
    </aside>
  );
}
