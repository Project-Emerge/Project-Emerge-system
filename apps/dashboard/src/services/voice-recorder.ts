/** Types a browser's MediaRecorder actually produces, in the order the gateway prefers them. */
const PREFERRED_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg", "audio/mp4"];

export type Recording = {
  /** Stops the recorder, releases the microphone, and resolves with what was captured. */
  stop(): Promise<Blob>;
  /** Releases the microphone and throws the audio away. */
  cancel(): void;
};

export type RecordingOptions = {
  /**
   * Silence duration in milliseconds required to trigger automatic stop after speech has been detected.
   * Defaults to 1500ms (1.5 seconds).
   * Set to 0 or negative to disable silence detection.
   */
  silenceTimeoutMs?: number;

  /**
   * Maximum recording duration in milliseconds before automatic stop.
   * Defaults to 15000ms (15 seconds).
   * Set to 0 or negative to disable maximum limit.
   */
  maxDurationMs?: number;

  /**
   * Timeout in milliseconds before auto-stopping if no speech is detected at all.
   * Defaults to 5000ms (5 seconds).
   * Set to 0 or negative to disable.
   */
  noSpeechTimeoutMs?: number;

  /**
   * RMS volume threshold above which audio is considered speech.
   * Defaults to 0.015.
   */
  speechThreshold?: number;

  /**
   * Callback invoked when automatic stop is triggered (via silence detection or timeout).
   */
  onAutoStop?: () => void;
};

export class MicrophoneUnavailableError extends Error {}
export class MicrophoneDeniedError extends Error {}

export function isVoiceSupported(): boolean {
  return typeof window !== "undefined"
    && typeof window.MediaRecorder !== "undefined"
    && Boolean(navigator.mediaDevices?.getUserMedia);
}

function pickMimeType(): string | undefined {
  // Chrome and Firefox disagree about which container they can produce, and Safari supports
  // neither of the first two, so ask rather than assume.
  return PREFERRED_TYPES.find((type) => MediaRecorder.isTypeSupported?.(type));
}

/**
 * Starts recording from the microphone with silence detection and timeout support.
 *
 * Distinguishes "this browser cannot" from "this person said no", because the two need different
 * things said to the operator: one is a dead end, the other is a permission prompt away.
 */
export async function startRecording(options?: RecordingOptions): Promise<Recording> {
  if (!isVoiceSupported()) {
    throw new MicrophoneUnavailableError("This browser cannot record audio.");
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    const name = (error as DOMException)?.name;
    if (name === "NotAllowedError" || name === "SecurityError") {
      throw new MicrophoneDeniedError("Microphone access was refused.");
    }
    throw new MicrophoneUnavailableError(
      error instanceof Error ? error.message : "No microphone is available.",
    );
  }

  const mimeType = pickMimeType();
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks: Blob[] = [];
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  });
  recorder.start();

  const silenceTimeoutMs = options?.silenceTimeoutMs ?? 1500;
  const maxDurationMs = options?.maxDurationMs ?? 15000;
  const noSpeechTimeoutMs = options?.noSpeechTimeoutMs ?? 5000;
  const speechThreshold = options?.speechThreshold ?? 0.015;
  const onAutoStop = options?.onAutoStop;

  let audioContext: AudioContext | null = null;
  let sourceNode: MediaStreamAudioSourceNode | null = null;
  let analyser: AnalyserNode | null = null;
  let vadInterval: ReturnType<typeof setInterval> | null = null;
  let maxTimer: ReturnType<typeof setTimeout> | null = null;
  let isDone = false;

  function cleanupAudio(): void {
    if (vadInterval !== null) {
      clearInterval(vadInterval);
      vadInterval = null;
    }
    if (maxTimer !== null) {
      clearTimeout(maxTimer);
      maxTimer = null;
    }
    try {
      sourceNode?.disconnect();
      analyser?.disconnect();
      if (audioContext && audioContext.state !== "closed") {
        void audioContext.close();
      }
    } catch {
      // Ignore audio cleanup errors
    }
    audioContext = null;
    sourceNode = null;
    analyser = null;
  }

  function release(): void {
    cleanupAudio();
    stream.getTracks().forEach((track) => track.stop());
  }

  function triggerAutoStop(): void {
    if (isDone) return;
    isDone = true;
    cleanupAudio();
    onAutoStop?.();
  }

  if (maxDurationMs > 0) {
    maxTimer = setTimeout(() => {
      triggerAutoStop();
    }, maxDurationMs);
  }

  const AudioContextClass = typeof window !== "undefined"
    ? (window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
    : undefined;

  if (AudioContextClass && silenceTimeoutMs > 0) {
    try {
      audioContext = new AudioContextClass();
      sourceNode = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      sourceNode.connect(analyser);

      const pcmBuffer = new Float32Array(analyser.fftSize);
      let hasSpoken = false;
      let consecutiveSpeechSamples = 0;
      let silenceStart: number | null = null;
      const startedAt = Date.now();

      vadInterval = setInterval(() => {
        if (isDone || !analyser) return;

        let rms = 0;
        if (typeof analyser.getFloatTimeDomainData === "function") {
          analyser.getFloatTimeDomainData(pcmBuffer);
          let sumSquares = 0;
          for (let i = 0; i < pcmBuffer.length; i++) {
            sumSquares += pcmBuffer[i] * pcmBuffer[i];
          }
          rms = Math.sqrt(sumSquares / pcmBuffer.length);
        } else if (typeof analyser.getByteFrequencyData === "function") {
          const byteBuffer = new Uint8Array(analyser.frequencyBinCount);
          analyser.getByteFrequencyData(byteBuffer);
          let sum = 0;
          for (let i = 0; i < byteBuffer.length; i++) {
            sum += byteBuffer[i];
          }
          rms = sum / (byteBuffer.length * 255);
        }

        const isSpeaking = rms >= speechThreshold;
        const now = Date.now();

        if (isSpeaking) {
          consecutiveSpeechSamples++;
          if (consecutiveSpeechSamples >= 2) {
            hasSpoken = true;
          }
          silenceStart = null;
        } else {
          consecutiveSpeechSamples = 0;
          if (hasSpoken) {
            if (silenceStart === null) {
              silenceStart = now;
            } else if (now - silenceStart >= silenceTimeoutMs) {
              triggerAutoStop();
            }
          } else if (noSpeechTimeoutMs > 0 && now - startedAt >= noSpeechTimeoutMs) {
            triggerAutoStop();
          }
        }
      }, 100);
    } catch {
      // Degrade gracefully if AudioContext initialization is not permitted in environment
    }
  }

  return {
    stop(): Promise<Blob> {
      isDone = true;
      cleanupAudio();
      return new Promise<Blob>((resolve, reject) => {
        if (recorder.state === "inactive") {
          release();
          resolve(new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" }));
          return;
        }
        recorder.addEventListener("error", () => {
          release();
          reject(new MicrophoneUnavailableError("The recording failed."));
        });
        recorder.addEventListener("stop", () => {
          release();
          resolve(new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" }));
        });
        // `stop` is the event name in the DOM spec, but MediaRecorder fires `stop` only after
        // flushing a final `dataavailable`, so both listeners are registered before stopping.
        recorder.stop();
      });
    },
    cancel(): void {
      isDone = true;
      cleanupAudio();
      if (recorder.state !== "inactive") recorder.stop();
      release();
    },
  };
}
