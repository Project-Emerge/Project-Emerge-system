import { describe, expect, it } from "vitest";
import { AIMessage, ToolMessage, type BaseMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { z } from "zod";
import { createFormationAgent, formationToolSchemas, type FormationCheckRequest } from "./chat-agent.js";
import type { DesignRecord } from "./design-log.js";
import type { FleetSnapshot } from "../shared/chat.js";
import { CUSTOM_SOURCE_MAX_LENGTH, type FormationCompileResult } from "../shared/protocol.js";

const starSource = [
  "Formation { ctx =>",
  "  (0 until ctx.count).toList.map { i =>",
  "    val angle = 2 * math.Pi * i / ctx.count",
  "    val r = 0.6 + 0.3 * math.cos(5 * angle)",
  "    (r * math.sin(angle), r * math.cos(angle))",
  "  }",
  "}",
].join("\n");

type ToolCall = { name: string; args: Record<string, unknown> };

/**
 * A chat model that answers with whatever the test dictates.
 *
 * Injected through `createFormationAgent`'s options, so these tests exercise the whole
 * tool-call-to-command path -- schema validation, clamping, leader resolution -- with no network
 * and no API key.
 */
function stubModel(reply: { text?: string; toolCall?: ToolCall }): BaseChatModel {
  const message = new AIMessage({
    content: reply.text ?? "",
    tool_calls: reply.toolCall
      ? [{ name: reply.toolCall.name, args: reply.toolCall.args, id: "call-1" }]
      : [],
  });
  const model = {
    bindTools: () => model,
    invoke: async () => message,
  };
  return model as unknown as BaseChatModel;
}

const fleet: FleetSnapshot = {
  robotIds: ["A1B2C3", "D4E5F6", "0A0B0C"],
  posedRobotIds: ["A1B2C3", "D4E5F6"],
  activeFormation: null,
};

function agentFor(reply: { text?: string; toolCall?: ToolCall }) {
  return createFormationAgent({ chatModel: stubModel(reply) });
}

describe("agente di chat delle formazioni", () => {
  it("traduce una formazione integrata nel comando corrispondente", async () => {
    const agent = agentFor({
      text: "Formazione a V attorno ad A1B2C3.",
      toolCall: {
        name: "apply_formation",
        args: { program: "vShape", anchor: "leader", leaderId: "A1B2C3", params: { interDistanceV: 0.5 } },
      },
    });
    const reply = await agent.run([{ role: "user", content: "fai una V" }], fleet);
    expect(reply.command).toEqual({
      program: "vShape",
      anchor: "leader",
      leaderId: "A1B2C3",
      params: { interDistanceV: 0.5 },
      custom: null,
    });
    expect(reply.requiresConfirmation).toBe(false);
    expect(reply.commandSummary).toBe("V formation around A1B2C3");
    expect(reply.reply).toBe("Formazione a V attorno ad A1B2C3.");
  });

  it("riporta un parametro fuori scala dentro l'intervallo pubblicato", async () => {
    // Un cerchio da 40 metri deve diventare un comando limitato, non un rifiuto.
    const agent = agentFor({
      toolCall: {
        name: "apply_formation",
        args: { program: "circleShape", anchor: "auto", leaderId: null, params: { radius: 40 } },
      },
    });
    const reply = await agent.run([{ role: "user", content: "cerchio enorme" }], fleet);
    expect(reply.command?.params).toEqual({ radius: 1.5 });
  });

  it("scarta un parametro che nessun programma legge", async () => {
    const agent = agentFor({
      toolCall: {
        name: "apply_formation",
        args: { program: "circleShape", anchor: "auto", leaderId: null, params: { radius: 0.7, wobble: 3 } },
      },
    });
    const reply = await agent.run([{ role: "user", content: "cerchio" }], fleet);
    expect(reply.command?.params).toEqual({ radius: 0.7 });
  });

  it("non pubblica un programma inventato", async () => {
    const agent = agentFor({
      text: "Ecco fatto.",
      toolCall: {
        name: "apply_formation",
        args: { program: "octagonShape", anchor: "auto", leaderId: null, params: {} },
      },
    });
    const reply = await agent.run([{ role: "user", content: "ottagono" }], fleet);
    expect(reply.command).toBeNull();
    expect(reply.requiresConfirmation).toBe(false);
    expect(reply.reply).toContain("Ecco fatto.");
  });

  it("non pubblica uno strumento che non conosce", async () => {
    const agent = agentFor({ toolCall: { name: "launch_missiles", args: {} } });
    const reply = await agent.run([{ role: "user", content: "?" }], fleet);
    expect(reply.command).toBeNull();
    expect(reply.reply).toContain("launch_missiles");
  });

  it("ricade su un'elezione quando il leader richiesto non ha posizione", async () => {
    // Radicare i gradienti su un robot assente bloccherebbe la formazione senza dirlo.
    const agent = agentFor({
      toolCall: {
        name: "apply_formation",
        args: { program: "circleShape", anchor: "leader", leaderId: "0A0B0C", params: {} },
      },
    });
    const reply = await agent.run([{ role: "user", content: "cerchio attorno a 0A0B0C" }], fleet);
    expect(reply.command?.anchor).toBe("auto");
    expect(reply.command?.leaderId).toBeNull();
    expect(reply.reply).toContain("0A0B0C");
  });

  it("ricade su un'elezione quando il leader non esiste affatto", async () => {
    const agent = agentFor({
      toolCall: {
        name: "apply_formation",
        args: { program: "circleShape", anchor: "leader", leaderId: "FFFFFF", params: {} },
      },
    });
    const reply = await agent.run([{ role: "user", content: "cerchio" }], fleet);
    expect(reply.command?.anchor).toBe("auto");
    expect(reply.command?.leaderId).toBeNull();
  });

  it("normalizza un id robot scritto in minuscolo", async () => {
    const agent = agentFor({
      toolCall: {
        name: "apply_formation",
        args: { program: "circleShape", anchor: "leader", leaderId: "a1b2c3", params: {} },
      },
    });
    const reply = await agent.run([{ role: "user", content: "cerchio" }], fleet);
    expect(reply.command?.leaderId).toBe("A1B2C3");
  });

  it("richiede conferma per una formazione scritta in Scala", async () => {
    const agent = agentFor({
      text: "Ho disegnato una stella.",
      toolCall: {
        name: "design_formation",
        args: { kind: "formation", label: "Stella", source: starSource, anchor: "auto", leaderId: null },
      },
    });
    const reply = await agent.run([{ role: "user", content: "disegna una stella" }], fleet);
    expect(reply.command?.program).toBe("custom");
    expect(reply.command?.custom).toEqual({ kind: "formation", source: starSource, label: "Stella" });
    // Il punto dell'intero flusso: codice nuovo aspetta un operatore.
    expect(reply.requiresConfirmation).toBe(true);
    expect(reply.commandSummary).toBe("Custom (Stella) around an elected leader");
  });

  it("accetta anche un programma aggregato intero", async () => {
    const agent = agentFor({
      toolCall: {
        name: "design_formation",
        args: { kind: "program", label: "Fermi", source: "new Stop()", anchor: "auto", leaderId: null },
      },
    });
    const reply = await agent.run([{ role: "user", content: "scrivi un programma che li ferma" }], fleet);
    expect(reply.command?.custom).toEqual({ kind: "program", source: "new Stop()", label: "Fermi" });
    expect(reply.requiresConfirmation).toBe(true);
  });

  it("rifiuta un disegno senza codice senza pubblicare nulla", async () => {
    const agent = agentFor({
      toolCall: { name: "design_formation", args: { kind: "formation", label: "Vuota", anchor: "auto", leaderId: null } },
    });
    const reply = await agent.run([{ role: "user", content: "forma" }], fleet);
    expect(reply.command).toBeNull();
    expect(reply.reply).toContain("source");
  });

  it("rifiuta un codice piu lungo di quanto il runtime compili", async () => {
    const agent = agentFor({
      toolCall: {
        name: "design_formation",
        args: { kind: "formation", label: "Enorme", source: "x".repeat(CUSTOM_SOURCE_MAX_LENGTH + 1), anchor: "auto", leaderId: null },
      },
    });
    const reply = await agent.run([{ role: "user", content: "forma gigante" }], fleet);
    expect(reply.command).toBeNull();
  });

  it("ferma la flotta senza ancoraggio ne geometria", async () => {
    const agent = agentFor({ text: "Fermi tutti.", toolCall: { name: "stop_fleet", args: {} } });
    const reply = await agent.run([{ role: "user", content: "stop" }], fleet);
    expect(reply.command).toEqual({
      program: "stop",
      anchor: "leader",
      leaderId: null,
      params: {},
      custom: null,
    });
    expect(reply.requiresConfirmation).toBe(false);
  });

  it("risponde senza comando quando il modello non chiama nessuno strumento", async () => {
    const agent = agentFor({ text: "Ci sono due robot posizionati." });
    const reply = await agent.run([{ role: "user", content: "quanti robot ci sono?" }], fleet);
    expect(reply.command).toBeNull();
    expect(reply.commandSummary).toBeNull();
    expect(reply.reply).toBe("Ci sono due robot posizionati.");
  });

  it("non lascia mai la trascrizione senza una risposta da mostrare", async () => {
    const agent = agentFor({});
    const reply = await agent.run([{ role: "user", content: "..." }], fleet);
    expect(reply.reply.length).toBeGreaterThan(0);
    expect(reply.command).toBeNull();
  });

  it("riassume il comando anche quando il modello non dice nulla", async () => {
    const agent = agentFor({
      toolCall: {
        name: "apply_formation",
        args: { program: "heartShape", anchor: "leader", leaderId: "D4E5F6", params: {} },
      },
    });
    const reply = await agent.run([{ role: "user", content: "cuore" }], fleet);
    expect(reply.reply).toBe("Heart around D4E5F6");
  });

  it("appiattisce una risposta consegnata a blocchi di contenuto", async () => {
    // Gemini puo' rispondere con blocchi anziche' con una stringa.
    const message = new AIMessage({
      content: [{ type: "text", text: "Fatto" }, { type: "text", text: " subito." }] as never,
      tool_calls: [],
    });
    const model = { bindTools: () => model, invoke: async () => message };
    const agent = createFormationAgent({ chatModel: model as unknown as BaseChatModel });
    const reply = await agent.run([{ role: "user", content: "ciao" }], fleet);
    expect(reply.reply).toBe("Fatto subito.");
  });

  it("dichiara soltanto costrutti che lo schema di Gemini accetta", () => {
    // Regressione reale: `z.record` compila in `propertyNames`, che le function declarations di
    // Gemini non conoscono, e l'intera richiesta torna 400 -- non degrada affatto.
    //
    // Una lista di campi ammessi, non di campi vietati: e' l'unica forma che intercetta anche il
    // prossimo costrutto non supportato senza che qualcuno lo debba prevedere.
    const allowed = new Set([
      "type", "format", "title", "description", "nullable", "enum", "items",
      "properties", "required", "propertyOrdering", "default", "anyOf", "example",
      "minItems", "maxItems", "minProperties", "maxProperties",
      "minimum", "maximum", "minLength", "maxLength", "pattern",
    ]);

    function fieldsOf(node: unknown, path: string, found: string[]): string[] {
      if (Array.isArray(node)) {
        node.forEach((item, index) => fieldsOf(item, `${path}[${index}]`, found));
        return found;
      }
      if (node === null || typeof node !== "object") return found;
      for (const [key, value] of Object.entries(node)) {
        // Il draft e' aggiunto solo alla radice e non viaggia dentro la richiesta.
        if (path === "" && key === "$schema") continue;
        if (!allowed.has(key)) found.push(`${path}.${key}`);
        // Sotto `properties` le chiavi sono nomi di parametri, non campi dello schema.
        if (key === "properties" && value && typeof value === "object") {
          Object.entries(value).forEach(([name, child]) => fieldsOf(child, `${path}.${name}`, found));
        } else {
          fieldsOf(value, `${path}.${key}`, found);
        }
      }
      return found;
    }

    Object.entries(formationToolSchemas()).forEach(([name, schema]) => {
      const json = z.toJSONSchema(schema, { io: "input" });
      expect(fieldsOf(json, "", []), `${name} declares fields Gemini has no place for`).toEqual([]);
    });
  });

  it("nomina esplicitamente i parametri di ogni strumento", () => {
    const schemas = formationToolSchemas();
    type ParamsSchema = { properties: { params: { properties: Record<string, unknown> } } };
    const apply = z.toJSONSchema(schemas.apply_formation, { io: "input" }) as unknown as ParamsSchema;
    // I nomi reali, cosi' il modello non li inventa.
    expect(Object.keys(apply.properties.params.properties)).toContain("radius");
    expect(Object.keys(apply.properties.params.properties)).toContain("interDistanceV");
    // I parametri della sola formazione disegnata non compaiono qui.
    expect(Object.keys(apply.properties.params.properties)).not.toContain("customScale");

    const design = z.toJSONSchema(schemas.design_formation, { io: "input" }) as unknown as ParamsSchema;
    expect(Object.keys(design.properties.params.properties)).toContain("customScale");
    expect(Object.keys(design.properties.params.properties)).toContain("customMaxRadius");
    // `ctx.phase` in a designed shape is paced by wavePeriod, so the agent must be able to set it;
    // without it a moving shape can only spin at the six-second default and smears into a blur.
    expect(Object.keys(design.properties.params.properties)).toContain("wavePeriod");
  });
});

describe("registrazione delle geometrie disegnate", () => {
  function agentRecording(reply: { text?: string; toolCall?: ToolCall }) {
    const records: DesignRecord[] = [];
    const agent = createFormationAgent({
      chatModel: stubModel(reply),
      onDesign: (record) => records.push(record),
    });
    return { agent, records };
  }

  it("annota il codice grezzo accanto al comando limitato", async () => {
    // Le due meta' insieme sono il punto: se la forma esce sbagliata, il registro dice se e' colpa
    // del codice o del limite che il gateway gli ha imposto.
    const { agent, records } = agentRecording({
      text: "Ecco la spirale.",
      toolCall: {
        name: "design_formation",
        args: {
          kind: "formation",
          label: "Spirale",
          source: starSource,
          anchor: "auto",
          leaderId: null,
          params: { customScale: 99 },
        },
      },
    });

    await agent.run(
      [{ role: "user", content: "una spirale" }, { role: "assistant", content: "ok" }, { role: "user", content: "riprova" }],
      fleet,
    );

    expect(records).toHaveLength(1);
    expect(records[0].prompt).toBe("riprova");
    expect(records[0].reply).toBe("Ecco la spirale.");
    expect(records[0].posedRobots).toBe(2);
    expect((records[0].raw as { params: { customScale: number } }).params.customScale).toBe(99);
    expect(records[0].command?.params.customScale).toBe(10);
    expect(records[0].error).toBeNull();
  });

  it("annota anche un disegno rifiutato, con il motivo", async () => {
    const { agent, records } = agentRecording({
      toolCall: { name: "design_formation", args: { kind: "formation", label: "Vuota", anchor: "auto", leaderId: null } },
    });

    await agent.run([{ role: "user", content: "forma" }], fleet);

    expect(records).toHaveLength(1);
    expect(records[0].command).toBeNull();
    expect(records[0].error).toContain("source");
  });

  describe("con il controllo del runtime", () => {
    const design = (source: string): ToolCall => ({
      name: "design_formation",
      args: { kind: "formation", label: "Stella", source, anchor: "auto", leaderId: null, params: { customScale: 1.5 } },
    });

    /** Answers each call with the next reply, and remembers what it was shown. */
    function scripted(replies: ToolCall[]) {
      const seen: BaseMessage[][] = [];
      const model = {
        bindTools: () => model,
        invoke: async (history: BaseMessage[]) => {
          seen.push(history);
          const call = replies[seen.length - 1];
          return new AIMessage({ content: "", tool_calls: [{ ...call, id: `call-${seen.length}` }] });
        },
      };
      return { model: model as unknown as BaseChatModel, seen };
    }

    function answers(results: Array<Pick<FormationCompileResult, "ok" | "problems">>) {
      const requests: FormationCheckRequest[] = [];
      const checkDesign = async (request: FormationCheckRequest): Promise<FormationCompileResult> => {
        requests.push(request);
        const next = results[requests.length - 1];
        return { id: `check-${requests.length}`, preview: null, ...next };
      };
      return { checkDesign, requests };
    }

    const clean = { ok: true, problems: [] };
    const typeError = { ok: false, problems: [{ line: 2, column: 18, message: "Found: String, Required: Double" }] };

    it("pubblica al primo colpo un disegno che il runtime compila", async () => {
      const { model, seen } = scripted([design(starSource)]);
      const { checkDesign, requests } = answers([clean]);
      const reply = await createFormationAgent({ chatModel: model, checkDesign }).run([{ role: "user", content: "stella" }], fleet);

      expect(seen).toHaveLength(1);
      // Il controllo vede la flotta reale: tutti i robot posizionati tranne l'ancora.
      expect(requests).toEqual([{ kind: "formation", source: starSource, slots: 1, params: { customScale: 1.5 } }]);
      expect(reply.command?.custom).toMatchObject({ source: starSource });
    });

    it("rimanda al modello gli errori del compilatore, una volta sola", async () => {
      const fixed = "Formation(ctx => ring(ctx.count, 0.5))";
      const { model, seen } = scripted([design("Formation { ctx =>\n  ring(ctx.count, \"far\")\n}"), design(fixed)]);
      const { checkDesign } = answers([typeError, clean]);
      const records: DesignRecord[] = [];
      const reply = await createFormationAgent({ chatModel: model, checkDesign, onDesign: (record) => records.push(record) })
        .run([{ role: "user", content: "stella" }], fleet);

      expect(seen).toHaveLength(2);
      // La seconda chiamata vede la propria richiesta e la risposta del compilatore, con la riga.
      const feedback = seen[1].at(-1);
      expect(feedback).toBeInstanceOf(ToolMessage);
      expect(String(feedback?.content)).toContain("line 2, column 18: Found: String, Required: Double");
      expect(reply.command?.custom).toMatchObject({ source: fixed });
      expect(records).toHaveLength(1);
      expect(records[0].attempts).toBe(2);
      expect(records[0].issues).toEqual([]);
    });

    it("non pubblica nulla se anche la correzione non compila", async () => {
      const { model, seen } = scripted([design("a"), design("b")]);
      const { checkDesign } = answers([typeError, typeError]);
      const records: DesignRecord[] = [];
      const reply = await createFormationAgent({ chatModel: model, checkDesign, onDesign: (record) => records.push(record) })
        .run([{ role: "user", content: "stella" }], fleet);

      expect(seen).toHaveLength(2);
      expect(reply.command).toBeNull();
      expect(reply.reply).toContain("Found: String");
      expect(records[0].command).toBeNull();
      expect(records[0].issues).toEqual(["line 2, column 18: Found: String, Required: Double"]);
    });

    it("non pubblica nulla se il runtime non risponde al controllo", async () => {
      const { model } = scripted([design(starSource)]);
      const checkDesign = async (): Promise<FormationCompileResult> => {
        throw new Error("the aggregate runtime did not answer within 15 s");
      };
      const reply = await createFormationAgent({ chatModel: model, checkDesign }).run([{ role: "user", content: "stella" }], fleet);
      expect(reply.command).toBeNull();
      expect(reply.reply).toContain("did not answer");
    });
  });

  it("non annota le formazioni integrate", async () => {
    // Un programma compilato e' gia' ricostruibile dal nome e dai parametri sul topic retained.
    const { agent, records } = agentRecording({
      toolCall: { name: "apply_formation", args: { program: "circleShape", anchor: "auto", leaderId: null } },
    });

    await agent.run([{ role: "user", content: "cerchio" }], fleet);

    expect(records).toEqual([]);
  });
});
