import {
  FORMATION_PROGRAMS,
  type FormationAnchor,
  type FormationCommand,
  type FormationProgram,
} from "./protocol.js";

export type FormationParamDefinition = {
  key: string;
  label: string;
  unit?: string;
  min: number;
  max: number;
  step: number;
  defaultValue: number;
};

/**
 * `custom` is deliberately its own group and absent from [[GROUP_ORDER]]: an operator cannot
 * author a geometry with sliders, so it gets no picker button. It still needs a definition so
 * that a formation the chat agent designed has a label for the toolbar and tunable parameters
 * once it is the active one.
 */
export type FormationGroup = "shape" | "dynamic" | "local" | "custom";

export type FormationDefinition = {
  value: FormationProgram;
  label: string;
  description: string;
  group: FormationGroup;
  /** Which frames of reference this program can be built in. Empty means it ignores the anchor. */
  anchors: FormationAnchor[];
  params: FormationParamDefinition[];
};

const COLLISION_AREA: FormationParamDefinition = {
  key: "collisionArea",
  label: "Collision radius",
  unit: "m",
  min: 0.05,
  max: 1,
  step: 0.05,
  defaultValue: 0.3,
};

const STABILITY_THRESHOLD: FormationParamDefinition = {
  key: "stabilityThreshold",
  label: "Stability threshold",
  unit: "m",
  min: 0.01,
  max: 0.5,
  step: 0.01,
  defaultValue: 0.1,
};

// Mean distance between two elected leaders, in hops. Above the fleet's hop diameter this
// elects exactly one; lowering it elects several, each growing its own regional formation.
const ELECTION_GRAIN: FormationParamDefinition = {
  key: "electionGrain",
  label: "Election grain",
  unit: "hops",
  min: 1,
  max: 12,
  step: 1,
  defaultValue: 8,
};

const RADIUS: FormationParamDefinition = {
  key: "radius",
  label: "Circle radius",
  unit: "m",
  min: 0.2,
  max: 1.5,
  step: 0.05,
  defaultValue: 0.6,
};

const WAVE_PERIOD: FormationParamDefinition = {
  key: "wavePeriod",
  label: "Cycle time",
  unit: "s",
  min: 1,
  max: 30,
  step: 0.5,
  defaultValue: 6,
};

const WAVE_AMPLITUDE: FormationParamDefinition = {
  key: "waveAmplitude",
  label: "Wave amplitude",
  unit: "m",
  min: 0.05,
  max: 0.6,
  step: 0.05,
  defaultValue: 0.2,
};

const WAVE_NUMBER: FormationParamDefinition = {
  key: "waveNumber",
  label: "Crests",
  min: 1,
  max: 4,
  step: 1,
  defaultValue: 1,
};

/** Live multiplier on a designed geometry, so a shape can be resized without redesigning it. */
const CUSTOM_SCALE: FormationParamDefinition = {
  key: "customScale",
  label: "Shape scale",
  min: 0.05,
  max: 10,
  step: 0.05,
  defaultValue: 1,
};

/**
 * How far a designed slot may sit from the anchor. Travels on the same message as the geometry,
 * so it guards a mistaken author rather than a malicious one; the ceiling no message can raise
 * is `CustomSlots.AbsoluteMaxRadius` in the aggregate runtime.
 */
const CUSTOM_MAX_RADIUS: FormationParamDefinition = {
  key: "customMaxRadius",
  label: "Max slot radius",
  unit: "m",
  min: 0.2,
  max: 3,
  step: 0.1,
  defaultValue: 1.5,
};

const ALL_ANCHORS: FormationAnchor[] = ["leader", "auto"];

export const ANCHOR_LABELS: Record<FormationAnchor, string> = {
  leader: "Chosen leader",
  auto: "Elected leader",
};

export const ANCHOR_DESCRIPTIONS: Record<FormationAnchor, string> = {
  leader: "You pick the robot the shape is built around.",
  auto: "The fleet elects its own leader, with no operator input.",
};

export const FORMATION_DEFINITIONS: FormationDefinition[] = [
  {
    value: "pointToLeader",
    label: "Point to leader",
    description: "Every robot turns to face the leader.",
    group: "shape",
    anchors: ["leader", "auto"],
    params: [ELECTION_GRAIN],
  },
  {
    value: "vShape",
    label: "V formation",
    description: "Two trailing arms fan out behind the anchor.",
    group: "shape",
    anchors: ALL_ANCHORS,
    params: [
      { key: "interDistanceV", label: "Arm spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      { key: "angleV", label: "Arm angle", unit: "rad", min: -Math.PI, max: Math.PI, step: 0.05, defaultValue: -0.79 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
      ELECTION_GRAIN,
    ],
  },
  {
    value: "lineShape",
    label: "Line",
    description: "Robots line up side by side around the anchor.",
    group: "shape",
    anchors: ALL_ANCHORS,
    params: [
      { key: "interDistanceLine", label: "Robot spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
      ELECTION_GRAIN,
    ],
  },
  {
    value: "circleShape",
    label: "Circle",
    description: "Robots ring the anchor at a fixed radius.",
    group: "shape",
    anchors: ALL_ANCHORS,
    params: [RADIUS, COLLISION_AREA, STABILITY_THRESHOLD, ELECTION_GRAIN],
  },
  {
    value: "squareShape",
    label: "Square",
    description: "Robots fill a grid around the anchor.",
    group: "shape",
    anchors: ALL_ANCHORS,
    params: [
      { key: "interDistanceSquare", label: "Grid spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
      ELECTION_GRAIN,
    ],
  },
  {
    value: "verticalLineShape",
    label: "Vertical line",
    description: "Robots queue directly behind the anchor.",
    group: "shape",
    anchors: ALL_ANCHORS,
    params: [
      { key: "interDistanceVertical", label: "Robot spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
      ELECTION_GRAIN,
    ],
  },
  {
    value: "heartShape",
    label: "Heart",
    description: "Robots trace a heart outline around the anchor.",
    group: "shape",
    anchors: ALL_ANCHORS,
    params: [
      { key: "scaleHeart", label: "Heart size", unit: "m", min: 0.02, max: 0.2, step: 0.01, defaultValue: 0.06 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
      ELECTION_GRAIN,
    ],
  },
  {
    value: "orbitCircle",
    label: "Orbit",
    description: "The ring keeps its spacing but turns steadily around the anchor.",
    group: "dynamic",
    anchors: ALL_ANCHORS,
    params: [RADIUS, WAVE_PERIOD, COLLISION_AREA, STABILITY_THRESHOLD, ELECTION_GRAIN],
  },
  {
    value: "breathingCircle",
    label: "Breathing circle",
    description: "The ring expands and contracts in unison.",
    group: "dynamic",
    anchors: ALL_ANCHORS,
    params: [RADIUS, WAVE_AMPLITUDE, WAVE_PERIOD, COLLISION_AREA, STABILITY_THRESHOLD, ELECTION_GRAIN],
  },
  {
    value: "ringWave",
    label: "Ring wave",
    description: "A crest of radius runs around the ring, robot after robot.",
    group: "dynamic",
    anchors: ALL_ANCHORS,
    params: [RADIUS, WAVE_AMPLITUDE, WAVE_NUMBER, WAVE_PERIOD, COLLISION_AREA, STABILITY_THRESHOLD, ELECTION_GRAIN],
  },
  {
    value: "sineLine",
    label: "Sine line",
    description: "Robots hold a line and ride a sine wave across it; the crest travels down the line.",
    group: "dynamic",
    anchors: ALL_ANCHORS,
    params: [
      { key: "interDistanceLine", label: "Robot spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      WAVE_AMPLITUDE,
      WAVE_NUMBER,
      WAVE_PERIOD,
      COLLISION_AREA,
      STABILITY_THRESHOLD,
      ELECTION_GRAIN,
    ],
  },
  {
    value: "stop",
    label: "Stop",
    description: "Every robot holds position.",
    group: "local",
    anchors: [],
    params: [],
  },
  {
    value: "custom",
    label: "Custom",
    description: "A geometry supplied as data rather than compiled in, designed in the swarm chat.",
    group: "custom",
    anchors: ALL_ANCHORS,
    // WAVE_PERIOD belongs here even though no compiled-in code reads it for this program: it is
    // what sets the rate of the `t` a designed formula sees, so without it a moving shape can
    // only ever spin at the default six seconds -- far faster than these robots can track, which
    // washes a rotating star out into a plain ring.
    params: [
      CUSTOM_SCALE,
      CUSTOM_MAX_RADIUS,
      WAVE_PERIOD,
      COLLISION_AREA,
      STABILITY_THRESHOLD,
      ELECTION_GRAIN,
    ],
  },
];

export const GROUP_LABELS: Record<FormationGroup, string> = {
  shape: "Static shapes",
  dynamic: "Moving shapes",
  local: "Control",
  custom: "Designed in chat",
};

/** Groups the picker renders, in order. `custom` is absent on purpose: it has no button. */
export const GROUP_ORDER: FormationGroup[] = ["shape", "dynamic", "local"];

export function definitionFor(program: FormationProgram): FormationDefinition {
  return FORMATION_DEFINITIONS.find((definition) => definition.value === program) ?? FORMATION_DEFINITIONS[0];
}

export function defaultParams(definition: FormationDefinition): Record<string, number> {
  return Object.fromEntries(definition.params.map((param) => [param.key, param.defaultValue]));
}

/** Falls back to the program's first supported anchor when the current one does not apply. */
export function resolveAnchor(definition: FormationDefinition, requested: FormationAnchor): FormationAnchor {
  if (definition.anchors.length === 0) return "leader";
  return definition.anchors.includes(requested) ? requested : definition.anchors[0];
}

export function getFormationLabel(program: string): string {
  return FORMATION_DEFINITIONS.find((definition) => definition.value === program)?.label ?? program;
}

/**
 * Every parameter any program accepts, keyed by molecule name.
 *
 * Two programs can share a molecule under different labels, so a shared key keeps the widest
 * range of the two: the value is legal for whichever program reads it.
 */
export const PARAM_RANGES: Record<string, { min: number; max: number }> = FORMATION_DEFINITIONS
  .flatMap((definition) => definition.params)
  .reduce<Record<string, { min: number; max: number }>>((ranges, param) => {
    const existing = ranges[param.key];
    ranges[param.key] = existing
      ? { min: Math.min(existing.min, param.min), max: Math.max(existing.max, param.max) }
      : { min: param.min, max: param.max };
    return ranges;
  }, {});

/**
 * Every parameter the given programs between them accept, sorted for a stable ordering.
 *
 * The chat agent's tool schemas enumerate these rather than accepting a free-form map: Gemini's
 * function-declaration schema has no `propertyNames`, which is what a zod record compiles to, and
 * naming the parameters also spares the model from inventing keys nothing reads.
 */
export function paramKeysFor(programs: readonly string[]): string[] {
  const wanted = new Set(programs);
  const keys = new Set(
    FORMATION_DEFINITIONS
      .filter((definition) => wanted.has(definition.value))
      .flatMap((definition) => definition.params.map((param) => param.key)),
  );
  return [...keys].sort();
}

/** `radius 0.2..1.5 m`, for a tool schema's per-parameter description. */
export function describeParam(key: string): string {
  const param = FORMATION_DEFINITIONS
    .flatMap((definition) => definition.params)
    .find((candidate) => candidate.key === key);
  const range = PARAM_RANGES[key];
  if (!param || !range) return key;
  return `${param.label}, ${range.min}..${range.max}${param.unit ? ` ${param.unit}` : ""}`;
}

/**
 * Drops unknown parameters and pulls known ones back inside their published range.
 *
 * The chat agent is the reason this exists: a model that asks for a 40 m circle should get a
 * clamped command rather than a rejected one, and a molecule no program reads should not reach
 * the runtime's config map, where it would linger forever (the map is merged, never replaced).
 */
export function clampParams(params: Record<string, number>): Record<string, number> {
  return Object.fromEntries(
    Object.entries(params).flatMap(([key, value]) => {
      const range = PARAM_RANGES[key];
      if (!range || !Number.isFinite(value)) return [];
      return [[key, Math.min(range.max, Math.max(range.min, value))]];
    }),
  );
}

/** Programs the chat agent may name, with everything it needs to choose between them. */
export function describeFormationsForPrompt(): string {
  return FORMATION_DEFINITIONS.map((definition) => {
    const anchors = definition.anchors.length === 0 ? "none (leaderless)" : definition.anchors.join(" | ");
    const params = definition.params.length === 0
      ? "none"
      : definition.params
        .map((param) => `${param.key} ${param.min}..${param.max}${param.unit ? ` ${param.unit}` : ""} (default ${param.defaultValue})`)
        .join(", ");
    return `- ${definition.value} — ${definition.description} anchors: ${anchors}. params: ${params}`;
  }).join("\n");
}

/** A one-line human summary of a command, for the chat's action chip. */
export function summariseCommand(command: FormationCommand): string {
  const label = getFormationLabel(command.program);
  const anchor = command.leaderId
    ? ` around ${command.leaderId}`
    : command.anchor === "auto" && definitionFor(command.program).anchors.length > 0
      ? " around an elected leader"
      : "";
  const geometry = command.custom?.label ? ` (${command.custom.label})` : "";
  return `${label}${geometry}${anchor}`;
}

/** Guards against a definition list that has drifted from the program enum. */
export function assertDefinitionsCoverEveryProgram(): void {
  const defined = new Set(FORMATION_DEFINITIONS.map((definition) => definition.value));
  const missing = FORMATION_PROGRAMS.filter((program) => !defined.has(program));
  if (missing.length > 0) {
    throw new Error(`Formation programs without a definition: ${missing.join(", ")}`);
  }
}
