import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChatPanel } from "./ChatPanel";
import { useDashboardStore } from "../store/dashboard-store";
import type { ChatReply } from "../../shared/chat";

const gateway = vi.hoisted(() => ({ publish: vi.fn().mockResolvedValue(undefined) }));

vi.mock("../services/gateway-context", () => ({
  useGatewayClient: () => gateway,
}));

const voice = vi.hoisted(() => ({
  isVoiceSupported: vi.fn(() => false),
  startRecording: vi.fn(),
}));

vi.mock("../services/voice-recorder", async () => {
  const actual = await vi.importActual<typeof import("../services/voice-recorder")>(
    "../services/voice-recorder",
  );
  return { ...actual, isVoiceSupported: voice.isVoiceSupported, startRecording: voice.startRecording };
});

const circleReply: ChatReply = {
  reply: "Ho messo tutti in cerchio.",
  command: { program: "circleShape", leaderId: null, anchor: "auto", params: { radius: 0.6 }, custom: null },
  commandSummary: "Circle around an elected leader",
  requiresConfirmation: false,
};

const starReply: ChatReply = {
  reply: "Ecco una stella.",
  command: {
    program: "custom",
    leaderId: null,
    anchor: "auto",
    params: {},
    custom: { kind: "polar", r: "0.6", theta: "2*pi*i/n", label: "Stella" },
  },
  commandSummary: "Custom (Stella) around an elected leader",
  requiresConfirmation: true,
};

/** Answers /api/chat/status once, then every /api/chat with the queued replies in order. */
function stubFetch(options: { enabled?: boolean; replies?: ChatReply[]; chatStatus?: number } = {}) {
  const replies = [...(options.replies ?? [])];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/chat/status") {
      return new Response(
        JSON.stringify({ enabled: options.enabled ?? true, model: "gemini-2.5-flash" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url === "/api/chat") {
      if (options.chatStatus && options.chatStatus >= 400) {
        return new Response(JSON.stringify({ error: "Il modello non risponde." }), {
          status: options.chatStatus,
          headers: { "Content-Type": "application/json" },
        });
      }
      const reply = replies.shift();
      if (!reply) throw new Error(`unexpected extra /api/chat call: ${String(init?.body)}`);
      return new Response(JSON.stringify(reply), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url === "/api/chat/transcribe") {
      return new Response(JSON.stringify({ text: "ferma tutti" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function ask(text: string): Promise<void> {
  fireEvent.change(screen.getByLabelText("Messaggio per lo sciame"), { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Invia"));
}

beforeEach(() => {
  useDashboardStore.setState({ connectionStatus: "connected" });
});

afterEach(() => {
  cleanup();
  gateway.publish.mockClear();
  voice.isVoiceSupported.mockReturnValue(false);
  voice.startRecording.mockReset();
  vi.unstubAllGlobals();
  useDashboardStore.getState().resetForTests();
});

describe("chat dello sciame", () => {
  it("mostra la risposta dell'agente nella trascrizione", async () => {
    stubFetch({ replies: [circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("metti tutti in cerchio");

    expect(await screen.findByText("Ho messo tutti in cerchio.")).toBeInTheDocument();
    expect(screen.getByText("metti tutti in cerchio")).toBeInTheDocument();
  });

  it("applica subito una formazione integrata, una volta sola", async () => {
    stubFetch({ replies: [circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("cerchio");

    await waitFor(() => expect(gateway.publish).toHaveBeenCalledTimes(1));
    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", {
      program: "circleShape",
      leaderId: null,
      anchor: "auto",
      params: { radius: 0.6 },
      custom: null,
    });
    expect(await screen.findByText(/Applicato Circle/)).toBeInTheDocument();
  });

  it("non pubblica nulla per una geometria inventata finche' non si conferma", async () => {
    stubFetch({ replies: [starReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("disegna una stella");

    // Il punto dell'intero flusso: una forma nuova aspetta un operatore.
    expect(await screen.findByText("Custom (Stella) around an elected leader")).toBeInTheDocument();
    expect(gateway.publish).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Applica" }));

    await waitFor(() => expect(gateway.publish).toHaveBeenCalledTimes(1));
    expect(gateway.publish).toHaveBeenCalledWith("/config/formation", starReply.command);
    expect(await screen.findByText(/Applicato Custom \(Stella\)/)).toBeInTheDocument();
  });

  it("scartare una geometria proposta non pubblica niente", async () => {
    stubFetch({ replies: [starReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("disegna una stella");
    fireEvent.click(await screen.findByRole("button", { name: "Ignora" }));

    expect(screen.getByText(/Scartato/)).toBeInTheDocument();
    expect(gateway.publish).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Applica" })).not.toBeInTheDocument();
  });

  it("annulla ripubblicando la formazione precedente", async () => {
    const previous = {
      program: "vShape" as const,
      leaderId: "A1B2C3",
      anchor: "leader" as const,
      params: { interDistanceV: 0.4 },
      custom: null,
    };
    useDashboardStore.setState({ connectionStatus: "connected", formation: previous });
    stubFetch({ replies: [circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("cerchio");
    fireEvent.click(await screen.findByRole("button", { name: "Annulla" }));

    await waitFor(() => expect(gateway.publish).toHaveBeenCalledTimes(2));
    expect(gateway.publish).toHaveBeenLastCalledWith("/config/formation", previous);
    expect(await screen.findByText(/Ripristinato a Formazione a V/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Annulla" })).not.toBeInTheDocument();
  });

  it("annullare senza una formazione precedente ferma la flotta", async () => {
    // La direzione sicura: meglio fermi che su una forma che l'operatore non ha scelto.
    stubFetch({ replies: [circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("cerchio");
    fireEvent.click(await screen.findByRole("button", { name: "Annulla" }));

    await waitFor(() => expect(gateway.publish).toHaveBeenCalledTimes(2));
    expect(gateway.publish).toHaveBeenLastCalledWith("/config/formation", {
      program: "stop",
      leaderId: null,
      anchor: "leader",
      params: {},
      custom: null,
    });
  });

  it("una risposta senza comando non pubblica nulla", async () => {
    stubFetch({
      replies: [{
        reply: "Ci sono due robot posizionati.",
        command: null,
        commandSummary: null,
        requiresConfirmation: false,
      }],
    });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("quanti robot ci sono?");

    expect(await screen.findByText("Ci sono due robot posizionati.")).toBeInTheDocument();
    expect(gateway.publish).not.toHaveBeenCalled();
  });

  it("spiega perche' e' disattivata quando manca la chiave", async () => {
    stubFetch({ enabled: false });
    render(<ChatPanel onClose={vi.fn()} />);

    expect(await screen.findByText(/GEMINI_API_KEY/)).toBeInTheDocument();
    expect(screen.getByLabelText("Messaggio per lo sciame")).toBeDisabled();
    expect(screen.getByLabelText("Invia")).toBeDisabled();
  });

  it("spiega perche' e' disattivata quando il gateway e' offline", async () => {
    useDashboardStore.setState({ connectionStatus: "offline" });
    stubFetch({});
    render(<ChatPanel onClose={vi.fn()} />);

    expect(await screen.findByText(/gateway è offline/)).toBeInTheDocument();
    expect(screen.getByLabelText("Messaggio per lo sciame")).toBeDisabled();
  });

  it("mostra l'errore del gateway senza toccare lo sciame", async () => {
    stubFetch({ chatStatus: 502 });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("cerchio");

    expect(await screen.findByText("Il modello non risponde.")).toBeInTheDocument();
    expect(gateway.publish).not.toHaveBeenCalled();
  });

  it("segnala una pubblicazione fallita senza vantare un comando applicato", async () => {
    gateway.publish.mockRejectedValueOnce(new Error("Broker non raggiungibile"));
    stubFetch({ replies: [circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("cerchio");

    expect(await screen.findByText(/Applicazione non riuscita/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Annulla" })).not.toBeInTheDocument();
  });

  it("chiude con Escape", async () => {
    const onClose = vi.fn();
    stubFetch({});
    render(<ChatPanel onClose={onClose} />);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });

  it("non mostra il microfono quando il browser non lo supporta", async () => {
    stubFetch({});
    render(<ChatPanel onClose={vi.fn()} />);

    expect(screen.queryByLabelText("Parla allo sciame")).not.toBeInTheDocument();
  });

  it("invia direttamente il messaggio dopo la trascrizione della registrazione vocale", async () => {
    voice.isVoiceSupported.mockReturnValue(true);
    const stop = vi.fn().mockResolvedValue(new Blob(["audio"], { type: "audio/webm" }));
    voice.startRecording.mockResolvedValue({ stop, cancel: vi.fn() });
    stubFetch({ replies: [circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByLabelText("Parla allo sciame"));
    await waitFor(() => expect(screen.getByLabelText("Ferma registrazione")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Ferma registrazione"));

    await waitFor(() =>
      expect(screen.getByText("ferma tutti")).toBeInTheDocument(),
    );
    await screen.findByText("Ho messo tutti in cerchio.");
    expect(gateway.publish).toHaveBeenCalledWith(
      "/config/formation",
      circleReply.command,
    );
  });

  it("invia automaticamente il messaggio quando scatta onAutoStop per silenzio o timer", async () => {
    voice.isVoiceSupported.mockReturnValue(true);
    const stop = vi.fn().mockResolvedValue(new Blob(["audio"], { type: "audio/webm" }));
    let autoStopCallback: (() => void) | undefined;
    voice.startRecording.mockImplementation((options?: { onAutoStop?: () => void }) => {
      autoStopCallback = options?.onAutoStop;
      return Promise.resolve({ stop, cancel: vi.fn() });
    });
    stubFetch({ replies: [circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByLabelText("Parla allo sciame"));
    await waitFor(() => expect(screen.getByLabelText("Ferma registrazione")).toBeInTheDocument());

    autoStopCallback?.();

    await waitFor(() =>
      expect(screen.getByText("ferma tutti")).toBeInTheDocument(),
    );
    await screen.findByText("Ho messo tutti in cerchio.");
    expect(gateway.publish).toHaveBeenCalledWith(
      "/config/formation",
      circleReply.command,
    );
  });

  it("mostra un errore e non invia nulla se la trascrizione e' vuota", async () => {
    voice.isVoiceSupported.mockReturnValue(true);
    const stop = vi.fn().mockResolvedValue(new Blob(["audio"], { type: "audio/webm" }));
    voice.startRecording.mockResolvedValue({ stop, cancel: vi.fn() });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/chat/status") {
        return new Response(JSON.stringify({ enabled: true, model: "gemini-2.5-flash" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/chat/transcribe") {
        return new Response(JSON.stringify({ text: "" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatPanel onClose={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("Parla allo sciame"));
    await waitFor(() => expect(screen.getByLabelText("Ferma registrazione")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Ferma registrazione"));

    await screen.findByText("Non ho capito. Riprova, oppure digita.");
    expect(gateway.publish).not.toHaveBeenCalled();
  });

  it("invia la conversazione intera, non solo l'ultimo turno", async () => {
    const fetchMock = stubFetch({ replies: [circleReply, circleReply] });
    render(<ChatPanel onClose={vi.fn()} />);

    await ask("cerchio");
    await screen.findByText("Ho messo tutti in cerchio.");
    await ask("piu' largo");
    await waitFor(() => expect(gateway.publish).toHaveBeenCalledTimes(2));

    const chatCalls = fetchMock.mock.calls.filter(([url]) => String(url) === "/api/chat");
    const lastBody = JSON.parse(String((chatCalls.at(-1)?.[1] as RequestInit).body));
    // "piu' largo" non significa nulla senza il turno che lo precede.
    expect(lastBody.messages).toEqual([
      { role: "user", content: "cerchio" },
      { role: "assistant", content: "Ho messo tutti in cerchio." },
      { role: "user", content: "piu' largo" },
    ]);
  });
});
