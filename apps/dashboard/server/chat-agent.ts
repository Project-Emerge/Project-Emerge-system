import { AIMessage, HumanMessage, SystemMessage, ToolMessage, type BaseMessage } from "@langchain/core/messages";
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
  CUSTOM_KINDS,
  CUSTOM_SOURCE_MAX_LENGTH,
  FORMATION_ANCHORS,
  FORMATION_PROGRAMS,
  FormationCommandSchema,
  type FormationCommand,
  type FormationCompileProblem,
  type FormationCompileRequest,
  type FormationCompileResult,
} from "../shared/protocol.js";
import type { DesignRecord } from "./design-log.js";
import { describeFleetForPrompt } from "./fleet-snapshot.js";

type ToolCall = NonNullable<AIMessage["tool_calls"]>[number];

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

const designFormationSchema = z.object({
  kind: z.enum(CUSTOM_KINDS)
    .describe("`formation` for a shape, an expression of type Formation; `program` for a whole aggregate program, an expression of type BaseDemo."),
  label: z.string().max(60).describe("A short name for the design, such as `Five-pointed star`."),
  // Optional in the schema so that a call without it gets a plain-spoken refusal naming it,
  // rather than the schema's generic one.
  source: z.string().max(CUSTOM_SOURCE_MAX_LENGTH).optional()
    .describe("The Scala source: one expression, of type Formation or BaseDemo as `kind` says."),
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

/** What the agent asks the runtime to compile: the gateway adds the id. */
export type FormationCheckRequest = Omit<FormationCompileRequest, "id">;

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
  /**
   * Compiles a design on the aggregate runtime before it is handed back to be published. Without
   * one the design goes out unchecked, and the runtime falls back to a ring if it fails there.
   */
  checkDesign?: (request: FormationCheckRequest) => Promise<FormationCompileResult>;
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
    "For a shape none of the built-ins produce, call design_formation with Scala source. The anchor",
    `robot stands at the origin and takes no slot, so the other robots fill ${slots} slots.`,
    "",
    "kind `formation`: the source is one Scala expression of type Formation, usually",
    "Formation { ctx => ... } returning a List[(Double, Double)] of exactly ctx.count slots, in metres",
    "from the anchor, x to the right and y straight ahead. In scope:",
    "- ctx.count (Int); ctx.phase (Double, the shared clock, 0 to 2*Pi once per wavePeriod seconds,",
    "  to make a shape move); ctx.param(name, default) for one of the live parameters.",
    "- ring(count, radius): count slots evenly round a circle, the first straight ahead.",
    "- line(count, spacing): count slots on a line through the anchor.",
    "- outline(count, closed)(points*): count slots along a path through [x, y] corners, a slot on",
    "  every corner when there are enough: letters, polygons, stars, arrows. Give a polygon as its",
    `  corners only, at most ${slots} of them. Robots stand on the outline only: never fill the inside.`,
    "- xs.rotated(angle), xs.scaled(k), xs.translated(dx, dy) on any List[(Double, Double)].",
    "- math.*, Math.PI, 0 until n, immutable collections, tuples, Option, lambdas and local defs.",
    "Nothing else is reachable: no I/O, no while loops, no try/catch, no mutable collections, and",
    "every call is metered, so a design that never finishes is stopped and replaced by a ring.",
    "Example: Formation { ctx => ring(ctx.count, 0.8).rotated(ctx.phase / 4) }",
    "",
    "kind `program`: the source is one expression of type BaseDemo, a whole ScaFi aggregate program",
    "that replaces the formation planner. Use it only for behaviour a shape cannot express. rep,",
    "nbr, foldhood, branch, mid, sense and the G, C and S blocks are available, as is ShapeFormation:",
    "  new ShapeFormation:",
    "    override protected def slots(ctx: SlotContext): List[(Double, Double)] =",
    "      ShapeFormation.ring(ctx.count, 0.0)(_ => 0.6)",
    "",
    "The runtime compiles the source before anything moves. If it refuses, you are shown its errors,",
    "with line and column, once: answer with a corrected design_formation call.",
    "",
    "Whenever a designed shape uses ctx.phase, wavePeriod is the seconds for one full cycle. The",
    "robots top out near 0.09 m/s, so the runtime stretches any cycle whose slots would move faster",
    "than about 0.06 m/s: a 0.35 m orbit takes about 40 s whatever wavePeriod says. Prefer small",
    "moves (0.05 to 0.15 m) over big ones, so the motion stays quick enough to read.",
    "",
    "Size a designed shape so its farthest point is about 0.8 m from the anchor, never under 0.4 m",
    "or over 1.2 m: smaller shapes look cramped. Centre the shape on the anchor and keep it at least",
    "0.3 m from the origin, since the anchor is a robot too. Aim for about 0.4 m between adjacent",
    "robots; closer than the collision radius plus twice the stability threshold (0.2 m by",
    "default) is grown apart by the runtime, so a small fleet cannot hold fine detail.",
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

function designFrom(args: z.infer<typeof designFormationSchema>): FormationCommand["custom"] | string {
  if (!args.source?.trim()) return "A design needs its Scala source.";
  return { kind: args.kind, source: args.source, label: args.label };
}

/** How many times the model may write a design: the first try and one correction. */
const MAX_DESIGN_ATTEMPTS = 2;

type Outcome = { command: FormationCommand; note: string | null } | { error: string };

/** As the author reads it, and as the model is shown it when asked to fix it. */
function describeProblem(problem: FormationCompileProblem): string {
  return problem.line > 0 ? `line ${problem.line}, column ${problem.column}: ${problem.message}` : problem.message;
}

/** The runtime's verdict on a design, as problems; empty when it compiled and laid out cleanly. */
async function verify(
  command: FormationCommand,
  fleet: FleetSnapshot,
  checkDesign: (request: FormationCheckRequest) => Promise<FormationCompileResult>,
): Promise<string[]> {
  if (!command.custom) return [];
  const result = await checkDesign({
    kind: command.custom.kind,
    source: command.custom.source,
    slots: Math.max(1, fleet.posedRobotIds.length - 1),
    params: command.params,
  });
  return result.ok ? [] : result.problems.map(describeProblem);
}

/**
 * The formation agent: one bounded model call, then whatever tool it asked for.
 *
 * Deliberately not an agent loop. These tools are effects the browser performs -- they publish to
 * the swarm -- rather than functions whose results the model needs to see, and the fleet's state
 * is already in the system prompt, so there is nothing to fetch and feed back. A single turn is
 * both simpler and far more predictable about what reaches the robots, and it keeps the chat
 * real-time. The one exception is a design the runtime refuses to compile: a compiler error is
 * precise enough to fix, so the model is shown it once and may answer with a corrected design.
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
      description: "Write a formation the built-ins cannot make, as Scala the runtime compiles.",
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

      let response = await bound.invoke(history);
      let spoken = textOf(response.content);
      let call = (response as AIMessage).tool_calls?.[0];

      if (!call) {
        return {
          reply: spoken || "I am not sure what to change. Could you say it another way?",
          command: null,
          commandSummary: null,
          requiresConfirmation: false,
        };
      }

      let outcome: Outcome = commandFor(call.name, call.args ?? {}, fleet);
      let attempts = 1;
      let issues: string[] = [];

      if (call.name === "design_formation" && options.checkDesign) {
        for (;;) {
          if ("error" in outcome) break;
          try {
            issues = await verify(outcome.command, fleet, options.checkDesign);
          } catch (error) {
            // No verdict is not a pass: the runtime is unreachable, and so are the robots.
            outcome = { error: `The design could not be checked: ${error instanceof Error ? error.message : String(error)}.` };
            break;
          }
          if (issues.length === 0 || attempts >= MAX_DESIGN_ATTEMPTS) break;

          history.push(response, new ToolMessage({
            tool_call_id: call.id ?? "design_formation",
            content: `The runtime refused this design. Fix it and call design_formation again.\n${issues.join("\n")}`,
          }));
          response = await bound.invoke(history);
          attempts += 1;
          spoken = textOf(response.content) || spoken;
          const retry: ToolCall | undefined = (response as AIMessage).tool_calls?.[0];
          if (!retry || retry.name !== "design_formation") {
            outcome = { error: "The runtime refused the design and no corrected one followed." };
            break;
          }
          call = retry;
          outcome = commandFor(call.name, call.args ?? {}, fleet);
        }
        if (!("error" in outcome) && issues.length > 0) {
          outcome = { error: `The runtime would not run this design: ${issues.join("; ")}` };
        }
      }

      // Only designs are recorded. The 17 built-ins are already on the retained `/config/formation`
      // topic and reproducible from their name and params; an invented design is not, and once
      // the next command overwrites it the only trace of what the fleet was asked to run is here.
      if (call.name === "design_formation" && options.onDesign) {
        options.onDesign({
          prompt: lastPrompt(messages),
          reply: spoken,
          posedRobots: fleet.posedRobotIds.length,
          raw: call.args ?? {},
          command: "error" in outcome ? null : outcome.command,
          error: "error" in outcome ? outcome.error : null,
          attempts,
          issues,
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

function commandFor(name: string, rawArgs: Record<string, unknown>, fleet: FleetSnapshot): Outcome {
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
    const design = designFrom(args.data);
    if (typeof design === "string") return { error: design };
    const { leaderId, anchor, note } = resolveLeader(args.data.leaderId, args.data.anchor, fleet);
    const built = buildCommand("custom", anchor, leaderId, numbersOnly(args.data.params), design);
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
