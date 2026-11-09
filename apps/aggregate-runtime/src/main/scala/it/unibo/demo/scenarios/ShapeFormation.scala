package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation

/** What a shape needs to lay out its slots: how many, the shared phase, the reference heading. */
final case class SlotContext(count: Int, phase: Double, reference: Double)

abstract class ShapeFormation() extends BaseDemo, SlotAssignment, FormationSteering:

  override def main(): Actuation = align(this.getClass)(_ => logic())

  def logic(): Actuation =
    val frame = anchorFrame
    actuate(frame, plan(frame))

  /** `ctx.count` slots around the anchor, none of them the origin: the anchor stands there. */
  protected def slots(ctx: SlotContext): List[(Double, Double)]

  /** Read from the shared clock, so it survives the loss of a robot's `rep` state. */
  protected def phase: Double =
    ShapeFormation.phaseAt(timestamp(), sense[Double](ShapeFormation.WavePeriod))

  /** [[phase]], its cycle stretched so a slot moving `travel` metres per radian stays followable. */
  protected def phaseFor(travel: Double): Double =
    val period = ShapeFormation.followablePeriod(
      sense[Double](ShapeFormation.WavePeriod),
      travel,
      sense[Double](ShapeFormation.SlotSpeed)
    )
    ShapeFormation.phaseAt(timestamp(), period)

  /** How close two slots may sit, the anchor included. */
  protected def clearance: Double =
    ShapeFormation.clearance(sense[Double](BaseDemo.CollisionArea), sense[Double](BaseDemo.StabilityThreshold))

  /**
   * The G/C/G sandwich: offsets collected at the root, matched there, plan broadcast back down.
   *
   * @return the vector from this robot to the slot it was assigned
   */
  private def plan(frame: AnchorFrame): (Double, Double) =
    val collected = collectCast[Map[Int, (Double, Double)]](
      frame.potential,
      _ ++ _,
      Map(mid() -> frame.toOrigin),
      Map.empty
    )
    val suggestion = branch(frame.isRoot) {
      // Only the root's copy is complete. Dropping it reserves the origin for itself.
      val participants = steadyParticipants(collected - mid())
      steadyAssignment(participants, slots(SlotContext(participants.size, phase, frame.reference)))
    }(Map.empty)
    gradientCast(frame.isRoot, suggestion, identity).getOrElse(mid(), (0.0, 0.0))

object ShapeFormation:

  /** Seconds for one full cycle of a time-varying shape. */
  val WavePeriod = "wavePeriod"

  /** How far, in metres, a time-varying shape departs from its rest position. */
  val WaveAmplitude = "waveAmplitude"

  /** How many crests fit across a travelling wave. */
  val WaveNumber = "waveNumber"

  /**
   * The fastest a time-varying slot may move, in m/s. Kept under the robots' top speed (a 3 cm
   * wheel at 60 rpm, about 0.094 m/s) to leave room for turning and catching up. A robot that
   * falls behind is handed the slot coming up from behind, and the motion never shows.
   */
  val SlotSpeed = "slotSpeed"

  /** Radius floor, so a pulsing ring can never invert through its own centre. */
  val MinRadius: Double = 0.05

  val DEFAULTS: Map[String, Double] = Map(
    WavePeriod -> 6.0,
    WaveAmplitude -> 0.1,
    WaveNumber -> 1.0,
    SlotSpeed -> 0.06
  )

  /** `period`, or the shortest one that keeps a slot moving `travel` metres per radian within `speed`. */
  def followablePeriod(period: Double, travel: Double, speed: Double): Double =
    if period <= 0.0 || !(speed > 0.0) || !travel.isFinite then period
    else math.max(period, 2 * math.Pi * math.abs(travel) / speed)

  /**
   * How close two slots may sit, the anchor included: the repulsion radius, plus how far short of
   * its slot each of the two robots may stop. Any closer and settled neighbours push each other off.
   */
  def clearance(collisionArea: Double, stabilityThreshold: Double): Double =
    val sum = collisionArea + 2 * stabilityThreshold
    if sum.isFinite then math.max(MinRadius, sum) else MinRadius

  /** Closest two of `points`. ponytail: quadratic, fine for a fleet of a few dozen. */
  def tightestPair(points: List[(Double, Double)]): Double =
    points
      .combinations(2)
      .collect { case List(a, b) => math.hypot(a._1 - b._1, a._2 - b._2) }
      .minOption
      .getOrElse(Double.PositiveInfinity)

  /** Smallest ring on which `count` evenly spaced slots keep `clearance` from each other and the anchor. */
  def minRingRadius(count: Int, clearance: Double): Double =
    if count < 2 then clearance else math.max(clearance, clearance / (2 * math.sin(math.Pi / count)))

  /** Phase of a `periodSeconds`-long cycle at the given shared-clock reading. */
  def phaseAt(timestampMillis: Long, periodSeconds: Double): Double =
    if periodSeconds <= 0.0 then 0.0
    else 2 * math.Pi * (timestampMillis / 1000.0) / periodSeconds

  /** `count` evenly spaced points on a ring, each with a radius that may depend on its index. */
  def ring(count: Int, bearingOffset: Double)(radiusAt: Int => Double): List[(Double, Double)] =
    if count <= 0 then List.empty
    else
      val division = (math.Pi * 2) / count
      (0 until count).map { i =>
        val angle = division * i + bearingOffset
        val r = radiusAt(i)
        (math.sin(angle) * r, math.cos(angle) * r)
      }.toList

  /** Signed positions along a line through the origin, end to end, skipping the origin itself. */
  def lineOffsets(count: Int, spacing: Double): List[Double] =
    if count <= 0 then List.empty
    else
      val left = count / 2
      val right = count - left
      ((left to 1 by -1).map(k => -k * spacing) ++ (1 to right).map(k => k * spacing)).toList

  /** Rotates a slot set about the origin of the frame. */
  def rotated(targets: List[(Double, Double)], angle: Double): List[(Double, Double)] =
    val cosTheta = math.cos(angle)
    val sinTheta = math.sin(angle)
    targets.map((x, y) => (x * cosTheta - y * sinTheta, x * sinTheta + y * cosTheta))
