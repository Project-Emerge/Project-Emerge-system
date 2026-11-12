import { afterEach, describe, expect, it, vi } from "vitest";
import { createFormationChecker } from "./formation-check.js";
import { FORMATION_COMPILE_REQUEST_TOPIC, formationCompileResultTopic } from "../shared/protocol.js";

type Published = { topic: string; payload: unknown };

function checker(timeoutMs = 1_000) {
  const published: Published[] = [];
  let next = 0;
  const instance = createFormationChecker({
    publish: (topic, payload) => {
      published.push({ topic, payload: JSON.parse(payload) });
    },
    timeoutMs,
    newId: () => `req-${++next}`,
  });
  return { instance, published };
}

const request = { kind: "formation" as const, source: "Formation(ctx => ring(ctx.count, 0.5))", slots: 4, params: {} };

describe("controllo del codice di una formazione presso il runtime", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("pubblica la richiesta e risolve con la risposta che porta il suo id", async () => {
    const { instance, published } = checker();
    const pending = instance.check(request);

    expect(published).toEqual([{ topic: FORMATION_COMPILE_REQUEST_TOPIC, payload: { id: "req-1", ...request } }]);
    const answer = { id: "req-1", ok: true, problems: [], preview: [[0, 0.5]] };
    expect(instance.receive(formationCompileResultTopic("req-1"), answer)).toBe(true);
    await expect(pending).resolves.toEqual(answer);
  });

  it("non confonde le risposte di richieste diverse", async () => {
    const { instance } = checker();
    const first = instance.check(request);
    const second = instance.check({ ...request, source: "Formation(ctx => Nil)" });

    instance.receive(formationCompileResultTopic("req-2"), { id: "req-2", ok: false, problems: [{ line: 1, column: 1, message: "no" }], preview: null });
    instance.receive(formationCompileResultTopic("req-1"), { id: "req-1", ok: true, problems: [], preview: null });

    await expect(first).resolves.toMatchObject({ ok: true });
    await expect(second).resolves.toMatchObject({ ok: false });
  });

  it("rifiuta una risposta malformata invece di fidarsene", async () => {
    const { instance } = checker();
    const pending = instance.check(request);
    instance.receive(formationCompileResultTopic("req-1"), { id: "req-1", ok: "yes" });
    await expect(pending).rejects.toThrow("malformed");
  });

  it("rinuncia se il runtime non risponde in tempo", async () => {
    vi.useFakeTimers();
    const { instance } = checker(15_000);
    const pending = instance.check(request);
    const outcome = expect(pending).rejects.toThrow("did not answer within 15 s");
    await vi.advanceTimersByTimeAsync(15_000);
    await outcome;
    // Una risposta tardiva non trova piu' nessuno ad aspettarla, e non rompe nulla.
    expect(instance.receive(formationCompileResultTopic("req-1"), { id: "req-1", ok: true, problems: [], preview: null })).toBe(true);
  });

  it("lascia passare i topic che non sono risposte di compilazione", () => {
    const { instance } = checker();
    expect(instance.receive("/pose/A1B2C3", {})).toBe(false);
    expect(instance.receive(FORMATION_COMPILE_REQUEST_TOPIC, {})).toBe(false);
  });
});
