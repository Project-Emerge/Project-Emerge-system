import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import type { FormationCommand } from "../shared/protocol.js";

/**
 * One designed geometry, as it was before and after the gateway had its say.
 *
 * `raw` is what the model actually wrote and `command` is what survived validation and clamping,
 * which is the whole reason this record exists: a shape that comes out wrong on the floor is
 * either a bad formula or a formula the gateway trimmed, and only having both sides distinguishes
 * the two. A refused design carries `command: null` and the reason instead.
 */
export type DesignRecord = {
  /** The operator turn that prompted the design. */
  prompt: string;
  /** What the model said in words. */
  reply: string;
  /** How many robots had a position when it designed, which is the `n` its formulas saw. */
  posedRobots: number;
  /** The tool arguments exactly as the model wrote them, before any validation. */
  raw: unknown;
  /** The command handed back to the browser, or null when the design was refused. */
  command: FormationCommand | null;
  /** Why it was refused, when it was. */
  error?: string | null;
  /** What [[inspectDesign]] found wrong with it, so a bad shape on the floor has a reason on record. */
  issues?: string[];
};

export type DesignLog = {
  /** Where the lines land, so the gateway can say it on startup. */
  path: string;
  record(entry: DesignRecord): void;
  /** Resolves once every recorded line has been written. For the tests, and for a clean stop. */
  flush(): Promise<void>;
};

/**
 * Appends every designed formation to a JSON-lines file the operator can read after the fact.
 *
 * Fire-and-forget by design: `record` returns nothing and a failed write only warns. The log is
 * there to explain a shape after it has been seen on the floor, so a full disk or an unwritable
 * path must never turn a working chat turn into a 502.
 *
 * Writes are chained rather than issued concurrently. Two designs can only overlap if two
 * operators talk to the gateway at once, but O_APPEND alone orders bytes, not lines, and one
 * interleaved line would corrupt exactly the record someone came here to read.
 */
export function createDesignLog(path: string): DesignLog {
  let pending: Promise<void> = Promise.resolve();
  let directoryReady = false;

  return {
    path,
    record(entry) {
      const line = `${JSON.stringify({ at: new Date().toISOString(), ...entry })}\n`;
      pending = pending
        .then(async () => {
          if (!directoryReady) {
            await mkdir(dirname(path), { recursive: true });
            directoryReady = true;
          }
          await appendFile(path, line, "utf8");
        })
        .catch((error: unknown) => {
          console.warn(
            `Could not write the custom formation log at ${path}:`,
            error instanceof Error ? error.message : error,
          );
        });
    },
    flush() {
      return pending;
    },
  };
}
