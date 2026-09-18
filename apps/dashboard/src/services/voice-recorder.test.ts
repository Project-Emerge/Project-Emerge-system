import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isVoiceSupported,
  MicrophoneDeniedError,
  MicrophoneUnavailableError,
  startRecording,
} from "./voice-recorder";

describe("voice-recorder", () => {
  const originalMediaRecorder = window.MediaRecorder;
  const originalMediaDevices = navigator.mediaDevices;
  const originalAudioContext = window.AudioContext;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.defineProperty(window, "MediaRecorder", {
      value: originalMediaRecorder,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: originalMediaDevices,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(window, "AudioContext", {
      value: originalAudioContext,
      configurable: true,
      writable: true,
    });
  });

  it("verifica se la registrazione vocale e' supportata", () => {
    expect(isVoiceSupported()).toBe(false);

    Object.defineProperty(window, "MediaRecorder", {
      value: vi.fn(),
      configurable: true,
      writable: true,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn() },
      configurable: true,
      writable: true,
    });

    expect(isVoiceSupported()).toBe(true);
  });

  it("lancia MicrophoneUnavailableError se il browser non supporta la registrazione", async () => {
    Object.defineProperty(window, "MediaRecorder", {
      value: undefined,
      configurable: true,
      writable: true,
    });
    await expect(startRecording()).rejects.toThrow(MicrophoneUnavailableError);
  });

  it("lancia MicrophoneDeniedError se l'utente rifiuta i permessi del microfono", async () => {
    Object.defineProperty(window, "MediaRecorder", {
      value: class {
        static isTypeSupported = vi.fn(() => true);
      },
      configurable: true,
      writable: true,
    });
    const notAllowedError = new DOMException("Permission denied", "NotAllowedError");
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn().mockRejectedValue(notAllowedError) },
      configurable: true,
      writable: true,
    });

    await expect(startRecording()).rejects.toThrow(MicrophoneDeniedError);
  });

  it("avvia la registrazione e la ferma restituendo il Blob audio", async () => {
    const trackStop = vi.fn();
    const mockStream = {
      getTracks: () => [{ stop: trackStop }],
    };

    let eventListeners: Record<string, ((event?: unknown) => void)[]> = {};
    class MockMediaRecorder {
      state = "recording";
      mimeType = "audio/webm";
      static isTypeSupported = vi.fn(() => true);
      addEventListener(event: string, cb: (event?: unknown) => void) {
        eventListeners[event] = eventListeners[event] || [];
        eventListeners[event].push(cb);
      }
      start = vi.fn();
      stop = vi.fn(() => {
        this.state = "inactive";
        eventListeners["dataavailable"]?.forEach((cb) => cb({ data: new Blob(["sample"], { type: "audio/webm" }) }));
        eventListeners["stop"]?.forEach((cb) => cb());
      });
    }

    Object.defineProperty(window, "MediaRecorder", {
      value: MockMediaRecorder,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn().mockResolvedValue(mockStream) },
      configurable: true,
      writable: true,
    });

    const recording = await startRecording();
    const blobPromise = recording.stop();
    const blob = await blobPromise;

    expect(blob).toBeInstanceOf(Blob);
    expect(trackStop).toHaveBeenCalled();
  });

  it("attiva onAutoStop dopo il timeout massimo impostato", async () => {
    const trackStop = vi.fn();
    const mockStream = {
      getTracks: () => [{ stop: trackStop }],
    };

    class MockMediaRecorder {
      state = "recording";
      mimeType = "audio/webm";
      static isTypeSupported = vi.fn(() => true);
      addEventListener = vi.fn();
      start = vi.fn();
      stop = vi.fn(() => {
        this.state = "inactive";
      });
    }

    Object.defineProperty(window, "MediaRecorder", {
      value: MockMediaRecorder,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn().mockResolvedValue(mockStream) },
      configurable: true,
      writable: true,
    });

    const onAutoStop = vi.fn();
    const recording = await startRecording({
      maxDurationMs: 3000,
      silenceTimeoutMs: 0,
      noSpeechTimeoutMs: 0,
      onAutoStop,
    });

    expect(onAutoStop).not.toHaveBeenCalled();

    vi.advanceTimersByTime(3000);

    expect(onAutoStop).toHaveBeenCalledTimes(1);

    recording.cancel();
  });
});
