import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDesignLog, type DesignRecord } from "./design-log.js";
import type { FormationCommand } from "../shared/protocol.js";

const command: FormationCommand = {
  program: "custom",
  anchor: "auto",
  leaderId: null,
  params: { customScale: 1 },
  custom: { kind: "polar", r: "0.6", theta: "2*pi*i/n", label: "Cerchio" },
};

function designed(overrides: Partial<DesignRecord> = {}): DesignRecord {
  return {
    prompt: "disegna un cerchio",
    reply: "Fatto.",
    posedRobots: 4,
    raw: { kind: "polar", r: "0.6", theta: "2*pi*i/n", label: "Cerchio" },
    command,
    error: null,
    ...overrides,
  };
}

async function temporaryPath(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), "emerge-design-log-"));
  return join(directory, "nested", "custom-formations.jsonl");
}

async function linesOf(path: string): Promise<Record<string, unknown>[]> {
  const text = await readFile(path, "utf8");
  return text.trimEnd().split("\n").map((line) => JSON.parse(line) as Record<string, unknown>);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("registro delle formazioni disegnate", () => {
  it("scrive una riga JSON per ogni geometria, creando la cartella", async () => {
    const path = await temporaryPath();
    const log = createDesignLog(path);

    log.record(designed());
    await log.flush();

    const [entry] = await linesOf(path);
    expect(entry.prompt).toBe("disegna un cerchio");
    expect(entry.command).toEqual(command);
    expect(entry.raw).toEqual(designed().raw);
    expect(entry.posedRobots).toBe(4);
    // Serve a rileggere il registro accanto a un video della flotta.
    expect(typeof entry.at).toBe("string");
    expect(Number.isNaN(Date.parse(String(entry.at)))).toBe(false);
  });

  it("registra anche un disegno rifiutato, con il motivo", async () => {
    const path = await temporaryPath();
    const log = createDesignLog(path);

    log.record(designed({ command: null, error: "custom.r: unknown function wobble" }));
    await log.flush();

    const [entry] = await linesOf(path);
    expect(entry.command).toBeNull();
    expect(entry.error).toBe("custom.r: unknown function wobble");
  });

  it("accoda in ordine senza mescolare le righe", async () => {
    const path = await temporaryPath();
    const log = createDesignLog(path);

    log.record(designed({ prompt: "primo" }));
    log.record(designed({ prompt: "secondo" }));
    log.record(designed({ prompt: "terzo" }));
    await log.flush();

    expect((await linesOf(path)).map((entry) => entry.prompt)).toEqual(["primo", "secondo", "terzo"]);
  });

  it("avverte e prosegue quando il percorso non e' scrivibile", async () => {
    // Un registro che non si puo' scrivere non deve far fallire il turno di chat che lo produce.
    const directory = await mkdtemp(join(tmpdir(), "emerge-design-log-"));
    const blocked = join(directory, "occupato");
    await writeFile(blocked, "non sono una cartella");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const log = createDesignLog(join(blocked, "custom-formations.jsonl"));

    log.record(designed());
    await expect(log.flush()).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledOnce();
  });
});
