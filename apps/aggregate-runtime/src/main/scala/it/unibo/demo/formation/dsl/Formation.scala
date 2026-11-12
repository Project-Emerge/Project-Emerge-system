package it.unibo.demo.formation.dsl

import it.unibo.demo.scenarios.{CustomSlots, ShapeFormation}

/** A slot offset from the anchor, in metres, in the runtime's frame. */
type Point = (Double, Double)

/**
 * What a formation is told each time it lays out its slots.
 *
 * @param count  slots to fill; the anchor stands on the origin and takes none
 * @param phase  shared clock phase in radians, advancing one turn per cycle
 * @param params the operator's live parameters, by molecule name
 */
final case class FormationContext(count: Int, phase: Double, params: Map[String, Double]):
  /** `default` when the operator never set `name`, or set it to something non-finite. */
  def param(name: String, default: Double): Double = params.get(name).filter(_.isFinite).getOrElse(default)

/** Geometry only: a formation decides where the slots are, never how robots reach them. */
trait Formation:
  def slots(ctx: FormationContext): List[Point]

object Formation:
  def apply(layout: FormationContext => List[Point]): Formation = ctx => layout(ctx)

/** `count` evenly spaced slots on a ring, in [[ShapeFormation.ring]]'s bearing convention. */
def ring(count: Int, radius: Double): List[Point] = ShapeFormation.ring(count, 0.0)(_ => radius)

/** `count` slots on a line through the anchor, end to end, skipping the anchor's own spot. */
def line(count: Int, spacing: Double): List[Point] = ShapeFormation.lineOffsets(count, spacing).map(x => (x, 0.0))

/** `count` slots along a path, a corner on every one of them when there are enough to go round. */
def outline(count: Int, closed: Boolean)(points: Point*): List[Point] =
  CustomSlots.resample(CustomSlots.dedupeConsecutive(points.toList), closed, count)

extension (slots: List[Point])
  def rotated(angle: Double): List[Point] = ShapeFormation.rotated(slots, angle)
  def scaled(factor: Double): List[Point] = slots.map((x, y) => (x * factor, y * factor))
  def translated(dx: Double, dy: Double): List[Point] = slots.map((x, y) => (x + dx, y + dy))
