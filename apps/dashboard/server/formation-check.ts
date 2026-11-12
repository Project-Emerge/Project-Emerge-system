import { randomUUID } from "node:crypto";
import {
  FORMATION_COMPILE_REQUEST_TOPIC,
  FormationCompileResultSchema,
  formationCompileResultTopic,
  isFormationCompileResultTopic,
  type FormationCompileRequest,
  type FormationCompileResult,
} from "../shared/protocol.js";

/** Long enough for the runtime's first, cold compile, which can take a couple of seconds. */
export const DEFAULT_CHECK_TIMEOUT_MS = 15_000;

export type FormationCheckerOptions = {
  /** Sends a request on the broker; the gateway hands it its MQTT client. */
  publish: (topic: string, payload: string) => void;
  timeoutMs?: number;
  /** Injected by the tests, so ids are predictable. */
  newId?: () => string;
};

export type FormationChecker = {
  /** Resolves with the runtime's verdict; rejects when it is malformed or never comes. */
  check(request: Omit<FormationCompileRequest, "id">): Promise<FormationCompileResult>;
  /**
   * Hands over a message from the broker. True when it was a compile answer, which the gateway
   * then keeps to itself: it is meant for whoever asked, not for every browser.
   */
  receive(topic: string, payload: unknown): boolean;
};

type Pending = {
  resolve: (result: FormationCompileResult) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

/**
 * Asks the aggregate runtime to compile a design before anything is published.
 *
 * Request and answer travel on their own non-retained topics, matched by id, so the checker needs
 * nothing from the broker but a publish and the answers routed back through [[receive]]: no
 * client of its own, and nothing to clean up but the timers of requests still in flight.
 */
export function createFormationChecker(options: FormationCheckerOptions): FormationChecker {
  const timeoutMs = options.timeoutMs ?? DEFAULT_CHECK_TIMEOUT_MS;
  const newId = options.newId ?? (() => randomUUID());
  const pending = new Map<string, Pending>();

  return {
    check(request) {
      const id = newId();
      return new Promise<FormationCompileResult>((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`the aggregate runtime did not answer within ${Math.round(timeoutMs / 1000)} s`));
        }, timeoutMs);
        pending.set(id, { resolve, reject, timer });
        try {
          options.publish(FORMATION_COMPILE_REQUEST_TOPIC, JSON.stringify({ id, ...request }));
        } catch (error) {
          clearTimeout(timer);
          pending.delete(id);
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      });
    },

    receive(topic, payload) {
      if (!isFormationCompileResultTopic(topic)) return false;
      const id = topic.slice(formationCompileResultTopic("").length);
      const waiting = pending.get(id);
      // Late, or someone else's: either way nobody here is waiting for it.
      if (!waiting) return true;
      pending.delete(id);
      clearTimeout(waiting.timer);
      const parsed = FormationCompileResultSchema.safeParse(payload);
      if (parsed.success && parsed.data.id === id) {
        waiting.resolve(parsed.data);
      } else {
        waiting.reject(new Error("the aggregate runtime sent a malformed compile answer"));
      }
      return true;
    },
  };
}
