import { defaultParams, definitionFor } from "../shared/formations.js";
import type { CustomFormationSpec } from "../shared/protocol.js";

/** Metres from the anchor a designed shape should reach. The prompt asks for it, the check holds it. */
export const DESIGN_EXTENT = { min: 0.4, max: 1.2 };

/** Twin of `CustomSlots.CornerAngle` in the aggregate runtime: a corner there is a corner here. */
const CORNER_ANGLE = (35 * Math.PI) / 180;

const EPSILON = 1e-6;

type Point = [number, number];

/** Consecutive duplicates and a closing duplicate dropped, as `CustomSlots.dedupeConsecutive` does. */
function dedupe(points: Point[]): Point[] {
  const kept = points.filter((point, index) => index === 0 || distance(point, points[index - 1]) >= EPSILON);
  return kept.length > 1 && distance(kept[0], kept[kept.length - 1]) < EPSILON ? kept.slice(0, -1) : kept;
}

/** Mirrors `CustomSlots.cornerIndices`: the vertices the runtime gives a slot of their own. */
export function cornerCount(raw: Point[], closed: boolean): number {
  const points = dedupe(raw);
  const size = points.length;
  if (size < 3) return size;
  const turn = (index: number) => {
    const [previous, here, next] = [points[(index - 1 + size) % size], points[index], points[(index + 1) % size]];
    const [ax, ay, bx, by] = [here[0] - previous[0], here[1] - previous[1], next[0] - here[0], next[1] - here[1]];
    return Math.abs(Math.atan2(ax * by - ay * bx, ax * bx + ay * by));
  };
  const interior = closed ? points.map((_, index) => index) : points.slice(1, -1).map((_, index) => index + 1);
  return interior.filter((index) => turn(index) >= CORNER_ANGLE).length + (closed ? 0 : 2);
}

/**
 * What would make a designed outline read badly on the floor: more corners than robots, too small
 * to see, or a point on the anchor. Only annotates the design log, so the chat stays one model
 * call; a correction round doubled the wait. Only `points`: a formula places each slot itself, and
 * judging one would need a second evaluator.
 * ponytail: formulas unchecked; port `Formula` if formula designs start coming out wrong.
 */
export function inspectDesign(
  custom: CustomFormationSpec,
  slots: number,
  params: Record<string, number>,
): string[] {
  if (custom.kind !== "points") return [];
  const tuned = { ...defaultParams(definitionFor("custom")), ...params };
  const points = custom.points.map(([x, y]): Point => [x * tuned.customScale, y * tuned.customScale]);
  const closed = custom.closed ?? false;
  const clearance = tuned.collisionArea + 2 * tuned.stabilityThreshold;
  const issues: string[] = [];

  const corners = cornerCount(points, closed);
  if (slots > 0 && corners > slots) {
    issues.push(
      `The outline has ${corners} corners but only ${slots} robots fill it, so some corners get no robot: give at most ${slots}.`,
    );
  }
  const reach = Math.max(...points.map(([x, y]) => Math.hypot(x, y)));
  if (reach < DESIGN_EXTENT.min) {
    issues.push(
      `It reaches only ${round(reach)} m from the anchor, too small to read: make it about twice as large, ${DESIGN_EXTENT.min} to ${DESIGN_EXTENT.max} m.`,
    );
  }
  if (reach > tuned.customMaxRadius) {
    issues.push(
      `It reaches ${round(reach)} m, past the ${tuned.customMaxRadius} m cap, so its far points get pulled in and distort it.`,
    );
  }
  // Vertices only: a corner is certain to get a slot there, while a side merely passing close to
  // the origin may put none near it, and the runtime nudges any that does.
  const nearest = Math.min(...points.map(([x, y]) => Math.hypot(x, y)));
  if (nearest < clearance) {
    issues.push(
      `A point sits ${round(nearest)} m from the anchor robot at the origin: move the shape so every point stays at least ${round(clearance)} m away.`,
    );
  }
  return issues;
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

function round(metres: number): number {
  return Math.round(metres * 100) / 100;
}
