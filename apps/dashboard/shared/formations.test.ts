import { describe, expect, it } from "vitest";
import {
  assertDefinitionsCoverEveryProgram,
  clampParams,
  defaultParams,
  definitionFor,
  describeFormationsForPrompt,
  FORMATION_DEFINITIONS,
  getFormationLabel,
  GROUP_ORDER,
  PARAM_RANGES,
  resolveAnchor,
  summariseCommand,
} from "./formations.js";
import { FORMATION_PROGRAMS, FormationCommandSchema } from "./protocol.js";

describe("metadati delle formazioni", () => {
  it("descrive ogni programma esattamente una volta", () => {
    expect(() => assertDefinitionsCoverEveryProgram()).not.toThrow();
    expect(FORMATION_DEFINITIONS).toHaveLength(FORMATION_PROGRAMS.length);
    const values = FORMATION_DEFINITIONS.map((definition) => definition.value);
    expect(new Set(values).size).toBe(values.length);
    // Nessuna definizione orfana: il pannello userebbe un pulsante impubblicabile.
    values.forEach((value) => expect(FORMATION_PROGRAMS).toContain(value));
  });

  it("mantiene ogni valore predefinito dentro il proprio intervallo", () => {
    FORMATION_DEFINITIONS.forEach((definition) => {
      definition.params.forEach((param) => {
        expect(param.min).toBeLessThan(param.max);
        expect(param.defaultValue).toBeGreaterThanOrEqual(param.min);
        expect(param.defaultValue).toBeLessThanOrEqual(param.max);
        expect(param.step).toBeGreaterThan(0);
      });
    });
  });

  it("produce parametri predefiniti che il contratto accetta", () => {
    FORMATION_DEFINITIONS.forEach((definition) => {
      const command = {
        program: definition.value,
        leaderId: definition.anchors.includes("leader") ? "A1B2C3" : null,
        anchor: definition.anchors.length > 0 ? definition.anchors[0] : "leader",
        params: defaultParams(definition),
        // Una geometria disegnata e' obbligatoria solo per `custom`.
        custom: definition.value === "custom"
          ? { kind: "polar" as const, r: "0.5", theta: "2*pi*i/n" }
          : null,
      };
      expect(FormationCommandSchema.safeParse(command).success).toBe(true);
    });
  });

  it("non offre un pulsante per la formazione disegnata in chat", () => {
    // `custom` ha una definizione -- serve l'etichetta e i cursori -- ma nessun gruppo
    // renderizzato: un operatore non puo' scrivere una geometria con i cursori.
    expect(definitionFor("custom").group).toBe("custom");
    expect(GROUP_ORDER).not.toContain("custom");
    const pickable = FORMATION_DEFINITIONS.filter((definition) => GROUP_ORDER.includes(definition.group));
    expect(pickable.map((definition) => definition.value)).not.toContain("custom");
    expect(pickable).toHaveLength(FORMATION_PROGRAMS.length - 1);
  });

  it("etichetta i programmi noti e restituisce il nome grezzo per gli altri", () => {
    expect(getFormationLabel("vShape")).toBe("V formation");
    expect(getFormationLabel("custom")).toBe("Custom");
    expect(getFormationLabel("octagonShape")).toBe("octagonShape");
  });

  it("ricade sul primo ancoraggio supportato quando quello richiesto non si applica", () => {
    expect(resolveAnchor(definitionFor("circleShape"), "auto")).toBe("auto");
    // I programmi senza leader ignorano l'ancoraggio e restano su "leader".
    expect(resolveAnchor(definitionFor("stop"), "auto")).toBe("leader");
    expect(resolveAnchor(definitionFor("pointToLeader"), "leader")).toBe("leader");
  });

  it("unisce gli intervalli di una molecola condivisa da piu' programmi", () => {
    // `waveNumber` e' letta sia dalla ringWave sia dalla sineLine, `interDistanceLine` sia
    // dalla linea statica sia dalla sineLine: l'intervallo condiviso deve accettare cio' che
    // ognuno dei programmi legge.
    expect(PARAM_RANGES.waveNumber).toEqual({ min: 1, max: 4 });
    expect(PARAM_RANGES.interDistanceLine).toEqual({ min: 0.1, max: 1.2 });
    expect(PARAM_RANGES.radius).toEqual({ min: 0.2, max: 1.5 });
  });

  it("riporta i parametri fuori scala dentro l'intervallo pubblicato", () => {
    expect(clampParams({ radius: 40 })).toEqual({ radius: 1.5 });
    expect(clampParams({ radius: -3 })).toEqual({ radius: 0.2 });
    expect(clampParams({ radius: 0.8 })).toEqual({ radius: 0.8 });
  });

  it("scarta i parametri che nessun programma legge", () => {
    // La mappa di configurazione del runtime viene unita e mai sostituita: una molecola
    // inventata resterebbe li' per sempre.
    expect(clampParams({ radius: 0.8, wobble: 3 })).toEqual({ radius: 0.8 });
    expect(clampParams({ radius: Number.NaN })).toEqual({});
    expect(clampParams({ radius: Number.POSITIVE_INFINITY })).toEqual({});
  });

  it("riassume un comando in una riga leggibile", () => {
    expect(summariseCommand({
      program: "heartShape", leaderId: "D4E5F6", anchor: "leader", params: {}, custom: null,
    })).toBe("Heart around D4E5F6");
    expect(summariseCommand({
      program: "circleShape", leaderId: null, anchor: "auto", params: {}, custom: null,
    })).toBe("Circle around an elected leader");
    // Un programma senza ancoraggio non deve millantare un leader eletto.
    expect(summariseCommand({
      program: "stop", leaderId: null, anchor: "auto", params: {}, custom: null,
    })).toBe("Stop");
    expect(summariseCommand({
      program: "custom",
      leaderId: null,
      anchor: "auto",
      params: {},
      custom: { kind: "polar", r: "0.5", theta: "2*pi*i/n", label: "Star" },
    })).toBe("Custom (Star) around an elected leader");
  });

  it("elenca per il modello ogni programma con ancoraggi e intervalli", () => {
    const prompt = describeFormationsForPrompt();
    FORMATION_PROGRAMS.forEach((program) => expect(prompt).toContain(program));
    expect(prompt).toContain("radius 0.2..1.5 m (default 0.6)");
    expect(prompt).toContain("none (leaderless)");
  });
});
