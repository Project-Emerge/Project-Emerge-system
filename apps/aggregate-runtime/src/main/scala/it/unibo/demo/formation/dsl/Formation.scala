package it.unibo.demo.formation.dsl

import it.unibo.demo.formation.Fuel
import it.unibo.demo.scenarios.{CustomSlots, ShapeFormation}

/** A slot offset from the anchor, in metres, in the runtime's frame. */
type Point = (Double, Double)

/**
 * What a formation is told each time it lays out its slots.
 *
 * @param count  slots to fill; the anchor stands on the origin and takes none
 * @param phase  shared clock phase in radians, wrapped to one turn, advancing once per cycle
 * @param lookup the operator's live parameters, by molecule name
 */
final case class FormationContext(count: Int, phase: Double, lookup: String => Option[Double]):
  /** `default` when the operator never set `name`, or set it to something non-finite. */
  def param(name: String, default: Double): Double = lookup(name).filter(_.isFinite).getOrElse(default)

object FormationContext:
  /** For a fixed set of parameters, as a check or a test has them. */
  def fromParams(count: Int, phase: Double, params: Map[String, Double]): FormationContext =
    FormationContext(count, phase, params.get)

/** Geometry only: a formation decides where the slots are, never how robots reach them. */
trait Formation:
  def slots(ctx: FormationContext): List[Point]

object Formation:
  def apply(layout: FormationContext => List[Point]): Formation = ctx => layout(ctx)

// Every builder below makes as many elements as a number it is handed, so it pays for all of
// them before making any: see Fuel.

/** `count` evenly spaced slots on a ring, in [[ShapeFormation.ring]]'s bearing convention. */
def ring(count: Int, radius: Double): List[Point] =
  Fuel.charge(math.max(0, count))
  ShapeFormation.ring(count, 0.0)(_ => radius)

/** `count` slots on a line through the anchor, end to end, skipping the anchor's own spot. */
def line(count: Int, spacing: Double): List[Point] =
  Fuel.charge(math.max(0, count))
  ShapeFormation.lineOffsets(count, spacing).map(x => (x, 0.0))

/** `count` slots along a path, a corner on every one of them when there are enough to go round. */
def outline(count: Int, closed: Boolean)(points: Point*): List[Point] =
  Fuel.charge(math.max(0, count).toLong + points.size)
  CustomSlots.resample(CustomSlots.dedupeConsecutive(points.toList), closed, count)

/**
 * `0 until n` and `1 to n` as authors write them, metered. They take precedence over the
 * standard library's, which [[it.unibo.demo.formation.SandboxPolicy]] refuses: a range is lazy,
 * but one `toList` on it builds every element without calling back into metered code.
 */
extension (start: Int)
  def until(end: Int): Range =
    Fuel.charge(math.max(0L, end.toLong - start))
    Range(start, end)

  def to(end: Int): Range =
    Fuel.charge(math.max(0L, end.toLong - start + 1))
    Range.inclusive(start, end)

extension (slots: List[Point])
  def rotated(angle: Double): List[Point] = ShapeFormation.rotated(slots, angle)
  def scaled(factor: Double): List[Point] = slots.map((x, y) => (x * factor, y * factor))
  def translated(dx: Double, dy: Double): List[Point] = slots.map((x, y) => (x + dx, y + dy))
