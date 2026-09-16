/** Types a browser's MediaRecorder actually produces, in the order the gateway prefers them. */
const PREFERRED_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg", "audio/mp4"];

export type Recording = {
  /** Stops the recorder, releases the microphone, and resolves with what was captured. */
  stop(): Promise<Blob>;
  /** Releases the microphone and throws the audio away. */
  cancel(): void;
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
 * Starts recording from the microphone.
 *
 * Distinguishes "this browser cannot" from "this person said no", because the two need different
 * things said to the operator: one is a dead end, the other is a permission prompt away.
 */
export async function startRecording(): Promise<Recording> {
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

  function release(): void {
    stream.getTracks().forEach((track) => track.stop());
  }

  return {
    stop(): Promise<Blob> {
      return new Promise<Blob>((resolve, reject) => {
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
      if (recorder.state !== "inactive") recorder.stop();
      release();
    },
  };
}
