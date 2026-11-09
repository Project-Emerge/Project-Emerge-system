import { AIMessage, HumanMessage, SystemMessage, type BaseMessage } from "@langchain/core/messages";
import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { StructuredToolParams } from "@langchain/core/tools";
import { ChatGoogleGenerativeAI } from "@langchain/google-genai";
import { z } from "zod";
import type { ChatMessage, ChatReply, FleetSnapshot } from "../shared/chat.js";
import {
  clampParams,
  describeFormationsForPrompt,
  describeParam,
  paramKeysFor,
  summariseCommand,
} from "../shared/formations.js";
import {
  CUSTOM_FORMULA_MAX_LENGTH,
  CUSTOM_MAX_COORDINATE,
  CUSTOM_MAX_POINTS,
  FORMATION_ANCHORS,
  FORMATION_PROGRAMS,
  FormationCommandSchema,
  type FormationCommand,
} from "../shared/protocol.js";
import { DESIGN_EXTENT, inspectDesign } from "./design-check.js";
import type { DesignRecord } from "./design-log.js";
import { describeFleetForPrompt } from "./fleet-snapshot.js";

/** Programs the agent picks from `apply_formation`; `custom` has its own tool. */
const APPLIABLE_PROGRAMS = FORMATION_PROGRAMS.filter((program) => program !== "custom");

const anchorSchema = z.enum(FORMATION_ANCHORS)
  .describe("`leader` builds the shape around leaderId; `auto` lets the fleet elect one.");

const leaderIdSchema = z.string().nullish()
  .describe("Six uppercase hex characters, from the list of located robots. Null for `auto`.");

/**
 * The parameters a tool accepts, as named optional numbers.
 *
 * Enumerated rather than left as a `z.record`, because zod compiles a record to `propertyNames`
 * and Gemini's function-declaration schema has no such field -- it rejects the whole request with
 * a 400. Naming them is better anyway: the model gets the real keys and their ranges instead of
 * guessing, and `clampParams` still drops anything stray.
 */
function paramsSchema(programs: readonly string[]) {
  const shape = Object.fromEntries(
    paramKeysFor(programs).map((key) => [key, z.number().describe(describeParam(key)).optional()]),
  );
  return z.object(shape)
    .describe("Only the parameters this program accepts. Omit any you do not want to change.")
    .optional();
}

const applyFormationSchema = z.object({
  program: z.enum(APPLIABLE_PROGRAMS as [string, ...string[]])
    .describe("One of the built-in formation programs."),
  anchor: anchorSchema,
  leaderId: leaderIdSchema,
  params: paramsSchema(APPLIABLE_PROGRAMS),
});

const pointSchema = z.array(z.number()).length(2);

const designFormationSchema = z.object({
  kind: z.enum(["points", "cartesian", "polar"])
    .describe("`points` for an explicit outline, `cartesian` or `polar` for formulas."),
  label: z.string().max(60).describe("A short name for the shape, such as `Five-pointed star`."),
  points: z.array(pointSchema).max(CUSTOM_MAX_POINTS).optional()
    .describe(`For \`points\`: [x, y] pairs in metres, each within +/-${CUSTOM_MAX_COORDINATE}.`),
  closed: z.boolean().optional()
    .describe("For `points`: true to walk the path back to its start, making a closed outline."),
  x: z.string().max(CUSTOM_FORMULA_MAX_LENGTH).optional().describe("For `cartesian`: x in metres."),
  y: z.string().max(CUSTOM_FORMULA_MAX_LENGTH).optional().describe("For `cartesian`: y in metres."),
  r: z.string().max(CUSTOM_FORMULA_MAX_LENGTH).optional().describe("For `polar`: radius in metres."),
  theta: z.string().max(CUSTOM_FORMULA_MAX_LENGTH).optional().describe("For `polar`: bearing in radians."),
  anchor: anchorSchema,
  leaderId: leaderIdSchema,
  params: paramsSchema(["custom"]),
});

const stopFleetSchema = z.object({});

/**
 * The tool declarations exactly as they are handed to the model.
 *
 * Exported so `chat-agent.test.ts` can compile them to JSON Schema and check them against the
 * subset Gemini accepts, which is narrower than JSON Schema proper and fails the whole request
 * with a 400 rather than degrading.
 */
export function formationToolSchemas(): Record<string, z.ZodTypeAny> {
  return {
    apply_formation: applyFormationSchema,
    design_formation: designFormationSchema,
    stop_fleet: stopFleetSchema,
  };
}

export type FormationAgentOptions = {
  apiKey?: string;
  model?: string;
  /** Injected by the tests in place of a real Gemini client. */
  chatModel?: BaseChatModel;
  /**
   * Where designed geometries are written down, if anywhere.
   *
   * A callback rather than the log itself, so this module stays free of the filesystem: the
   * gateway hands it [[DesignLog.record]] and the tests hand it an array's push.
   */
  onDesign?: (record: DesignRecord) => void;
};

export type FormationAgent = {
  run(messages: ChatMessage[], fleet: FleetSnapshot): Promise<ChatReply>;
};

export const DEFAULT_CHAT_MODEL = "gemini-2.5-flash";

function systemPrompt(fleet: FleetSnapshot): string {
  const slots = Math.max(0, fleet.posedRobotIds.length - 1);
  return [
    "You steer a swarm of small differential-drive robots on a flat indoor arena a few metres",
    "across. An operator talks to you and you choose a formation for the fleet.",
    "",
    "Reply with one short sentence saying what you did, in the operator's own language. Call at",
    "most one tool. When the request is a question, or too vague to act on, answer it and call no",
    "tool at all rather than guessing. Nothing reaches the robots without a tool call, so never say",
    "you drew or changed a formation unless you called the tool that does it.",
    "",
    "Built-in formations:",
    describeFormationsForPrompt(),
    "",
    "Anchor rules: a program listing anchors accepts either `leader` with a leaderId taken from",
    "the located robots below, or `auto` to let the fleet elect one. Prefer `auto` unless the",
    "operator names a robot. Programs with no anchors ignore both.",
    "",
    "For a shape none of the built-ins produce, call design_formation. The anchor robot stands at",
    `the origin and takes no slot, so the other robots fill ${slots} slots.`,
    "- `points`: an outline as [x, y] pairs in metres, for anything with corners: letters,",
    "  polygons, stars, arrows. Every corner gets a robot of its own when there are at least as many",
    "  slots as corners, and the spare robots spread evenly along the longest sides. So give a",
    `  polygon as its corners only, at most ${slots} of them, and not one point per robot. A curve given`,
    "  as more points than slots is sampled at equal spacing instead. Robots stand on the outline",
    "  only: never fill the inside. Set closed for a loop.",
    "- `cartesian`: formulas for x and y. `polar`: formulas for r and theta, where theta is a",
    "  bearing in radians with x = r*sin(theta) and y = r*cos(theta). Use these for smooth curves",
    "  and for motion.",
    "",
    "Formula syntax: + - * / % ^ and parentheses, with the variables i (0-based slot index),",
    "n (number of slots) and t (a shared clock phase that runs 0 to 2*pi once per wavePeriod",
    "seconds; use it to make a shape move). Constants pi and e. Functions sin cos tan asin acos",
    "atan atan2 sqrt abs floor ceil round exp log sign hypot min max. Nothing else parses.",
    "",
    "Whenever a designed shape uses t, wavePeriod is the seconds for one full cycle. The robots top",
    "out near 0.09 m/s, so the runtime stretches any cycle whose slots would move faster than about",
    "0.06 m/s: a 0.35 m orbit takes about 40 s whatever wavePeriod says. Prefer small moves",
    "(0.05 to 0.15 m) over big ones, so the motion stays quick enough to read.",
    "",
    "Size a designed shape so its farthest point is about 0.8 m from the anchor, never under",
    `${DESIGN_EXTENT.min} m or over ${DESIGN_EXTENT.max} m: smaller shapes look cramped. Centre the shape on the anchor and keep the`,
    "outline at least 0.3 m from the origin, since the anchor is a robot too. Aim for about 0.4 m",
    "between adjacent robots; closer than the collision radius plus twice the stability threshold",
    "(0.2 m by default) is grown apart by the runtime, so a small fleet cannot hold fine detail.",
    "",
    "The fleet right now:",
    describeFleetForPrompt(fleet),
  ].join("\n");
}

/** A leader is only usable if a robot with that id actually has a position. */
function resolveLeader(
  requested: string | null | undefined,
  anchor: "leader" | "auto",
  fleet: FleetSnapshot,
): { leaderId: string | null; anchor: "leader" | "auto"; note: string | null } {
  if (anchor === "auto") return { leaderId: null, anchor, note: null };
  const wanted = requested?.toUpperCase() ?? null;
  if (wanted && fleet.posedRobotIds.includes(wanted)) {
    return { leaderId: wanted, anchor: "leader", note: null };
  }
  // Falling back to an election beats rooting every gradient on a robot that is not there, which
  // would leave the formation without a source and freeze it.
  return {
    leaderId: null,
    anchor: "auto",
    note: wanted
      ? `${wanted} has no known position, so the fleet is electing its own leader instead.`
      : null,
  };
}

/** The enumerated params schema leaves omitted keys `undefined`; drop them before clamping. */
function numbersOnly(params: Record<string, number | undefined> | undefined): Record<string, number> {
  return Object.fromEntries(
    Object.entries(params ?? {}).filter((entry): entry is [string, number] => typeof entry[1] === "number"),
  );
}

function buildCommand(
  program: string,
  anchor: "leader" | "auto",
  leaderId: string | null,
  params: Record<string, number>,
  custom: FormationCommand["custom"],
): { command: FormationCommand } | { error: string } {
  const parsed = FormationCommandSchema.safeParse({
    program,
    anchor,
    leaderId,
    params: clampParams(params),
    custom: custom ?? null,
  });
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? "That formation is not valid." };
  }
  return { command: parsed.data };
}

function geometryFrom(args: z.infer<typeof designFormationSchema>): FormationCommand["custom"] | string {
  if (args.kind === "points") {
    if (!args.points || args.points.length === 0) return "A points geometry needs at least one [x, y] pair.";
    return {
      kind: "points",
      points: args.points.map((pair) => [pair[0], pair[1]] as [number, number]),
      closed: args.closed ?? false,
      label: args.label,
    };
  }
  if (args.kind === "cartesian") {
    if (!args.x || !args.y) return "A cartesian geometry needs both an x and a y formula.";
    return { kind: "cartesian", x: args.x, y: args.y, label: args.label };
  }
  if (!args.r || !args.theta) return "A polar geometry needs both an r and a theta formula.";
  return { kind: "polar", r: args.r, theta: args.theta, label: args.label };
}

/**
 * The formation agent: one bounded model call, then whatever tool it asked for.
 *
 * Deliberately not an agent loop. These tools are effects the browser performs -- they publish to
 * the swarm -- rather than functions whose results the model needs to see, and the fleet's state
 * is already in the system prompt, so there is nothing to fetch and feed back. A single turn is
 * both simpler and far more predictable about what reaches the robots, and it keeps the chat
 * real-time: a second round to correct a design doubles the wait, so [[inspectDesign]] only
 * annotates the design log.
 *
 * Nothing the model returns is trusted: every command is rebuilt through
 * [[FormationCommandSchema]] and clamped against the published parameter ranges, so an invented
 * program name or a forty-metre circle becomes a plain-spoken refusal rather than a publish.
 */
export function createFormationAgent(options: FormationAgentOptions): FormationAgent {
  const model = options.chatModel ?? createGeminiModel(options);

  // Declared, not implemented: the agent never runs a tool, it reads the arguments the model
  // chose and hands them back for the browser to publish.
  const tools: StructuredToolParams[] = [
    {
      name: "apply_formation",
      description: "Put the fleet into one of the built-in formations.",
      schema: applyFormationSchema,
    },
    {
      name: "design_formation",
      description: "Design a formation the built-ins cannot make, from an outline or from formulas.",
      schema: designFormationSchema,
    },
    {
      name: "stop_fleet",
      description: "Halt every robot where it stands.",
      schema: stopFleetSchema,
    },
  ];

  const bound = model.bindTools?.(tools) ?? model;

  return {
    async run(messages, fleet) {
      const history: BaseMessage[] = [new SystemMessage(systemPrompt(fleet))];
      for (const message of messages) {
        history.push(
          message.role === "user"
            ? new HumanMessage(message.content)
            : new AIMessage(message.content),
        );
      }

      const response = await bound.invoke(history);
      const spoken = textOf(response.content);
      const call = (response as AIMessage).tool_calls?.[0];

      if (!call) {
        return {
          reply: spoken || "I am not sure what to change. Could you say it another way?",
          command: null,
          commandSummary: null,
          requiresConfirmation: false,
        };
      }

      const outcome = commandFor(call.name, call.args ?? {}, fleet);

      // Only designs are recorded. The 17 built-ins are already on the retained `/config/formation`
      // topic and reproducible from their name and params; an invented geometry is not, and once
      // the next command overwrites it the only trace of what the fleet was asked to draw is here.
      if (call.name === "design_formation" && options.onDesign) {
        options.onDesign({
          prompt: lastPrompt(messages),
          reply: spoken,
          posedRobots: fleet.posedRobotIds.length,
          raw: call.args ?? {},
          command: "error" in outcome ? null : outcome.command,
          error: "error" in outcome ? outcome.error : null,
          issues: "error" in outcome || !outcome.command.custom
            ? []
            : inspectDesign(outcome.command.custom, fleet.posedRobotIds.length - 1, outcome.command.params),
        });
      }

      if ("error" in outcome) {
        // The model asked for something impossible. Say so instead of publishing it.
        return {
          reply: [spoken, outcome.error].filter(Boolean).join(" ").trim(),
          command: null,
          commandSummary: null,
          requiresConfirmation: false,
        };
      }

      const { command, note } = outcome;
      return {
        reply: [spoken || summariseCommand(command), note].filter(Boolean).join(" ").trim(),
        command,
        commandSummary: summariseCommand(command),
        // A novel geometry waits for an operator; the built-ins apply and offer an undo.
        requiresConfirmation: command.program === "custom",
      };
    },
  };
}

function commandFor(
  name: string,
  rawArgs: Record<string, unknown>,
  fleet: FleetSnapshot,
): { command: FormationCommand; note: string | null } | { error: string } {
  if (name === "stop_fleet") {
    const built = buildCommand("stop", "leader", null, {}, null);
    return "error" in built ? built : { command: built.command, note: null };
  }

  if (name === "apply_formation") {
    const args = applyFormationSchema.safeParse(rawArgs);
    if (!args.success) {
      return { error: `I could not read that formation request: ${args.error.issues[0]?.message}` };
    }
    const { leaderId, anchor, note } = resolveLeader(args.data.leaderId, args.data.anchor, fleet);
    const built = buildCommand(args.data.program, anchor, leaderId, numbersOnly(args.data.params), null);
    return "error" in built ? built : { command: built.command, note };
  }

  if (name === "design_formation") {
    const args = designFormationSchema.safeParse(rawArgs);
    if (!args.success) {
      return { error: `I could not read that design: ${args.error.issues[0]?.message}` };
    }
    const geometry = geometryFrom(args.data);
    if (typeof geometry === "string") return { error: geometry };
    const { leaderId, anchor, note } = resolveLeader(args.data.leaderId, args.data.anchor, fleet);
    const built = buildCommand("custom", anchor, leaderId, numbersOnly(args.data.params), geometry);
    return "error" in built ? built : { command: built.command, note };
  }

  return { error: `I do not know how to ${name}.` };
}

/** The turn that prompted the design, which is the half of the record a person reads first. */
function lastPrompt(messages: ChatMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === "user") return messages[index].content;
  }
  return "";
}

/** Flattens the model's reply, which may arrive as content blocks rather than a bare string. */
function textOf(content: unknown): string {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content
    .map((block) =>
      typeof block === "string"
        ? block
        : typeof block === "object" && block !== null && "text" in block
          ? String((block as { text: unknown }).text)
          : "",
    )
    .join("")
    .trim();
}

function createGeminiModel(options: FormationAgentOptions): BaseChatModel {
  if (!options.apiKey) {
    throw new Error("A Gemini API key is required to run the swarm chat.");
  }
  return new ChatGoogleGenerativeAI({
    model: options.model ?? DEFAULT_CHAT_MODEL,
    apiKey: options.apiKey,
    // The operator wants the same phrasing to give the same formation twice running.
    temperature: 0,
  }) as unknown as BaseChatModel;
}
