package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation, Stop}
import it.unibo.demo.robot.{DifferentialDrive, DriveConfig}
import it.unibo.scafi.space.Point3D
import it.unibo.scafi.space.optimization.RichPoint3D

/**
 * Everything a shape needs to lay out its slots.
 *
 * @param count     how many slots to produce
 * @param phase     advances with the shared clock; ignored by the static shapes
 * @param reference the formation's reference heading, in world axes and radians
 */
final case class SlotContext(count: Int, phase: Double, reference: Double)

abstract class ShapeFormation() extends BaseDemo:
  private val repulsionStrength = 0.6
  private val maxRepulsion = 2

  extension(p: Point3D)
    def magnitude: Double = p.distance(Point3D.Zero)
    def normalize: Point3D =
      val m = p.magnitude
      if m < 1e-9 then Point3D.Zero else Point3D(p.x/m, p.y/m, 0)

  override def main(): Actuation =
    align(this.getClass) {
      _ => logic()
    }

  def logic(): Actuation =
    val frame = anchorFrame
    actuate(frame, plan(frame))

  /**
   * The shape itself: `ctx.count` slot positions around the anchor robot, none of which is
   * the origin -- the anchor stands there.
   */
  protected def slots(ctx: SlotContext): List[(Double, Double)]

  /**
   * Collects every robot's offset to the origin at the root, solves the assignment there
   * once, and broadcasts the plan back down -- the G/C/G sandwich this runtime is built on.
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
    // Only the root's copy of `collected` is complete, and only the root uses it. The root
    // itself occupies the origin of the shape, so dropping `mid()` here both reserves the
    // centre for it and keeps it out of the assignment.
    val suggestion = branch(frame.isRoot) {
      val participants = steadyParticipants(collected.filter(_._1 != mid()))
      steadyAssignment(participants, slots(SlotContext(participants.size, phase, frame.reference)))
    }(Map.empty)
    gradientCast(frame.isRoot, suggestion, a => a).getOrElse(mid(), (0.0, 0.0))

  /**
   * Who the shape is laid out for: everyone the collect reached this round, plus anyone it
   * reached recently enough to be treated as briefly out of sight rather than gone.
   *
   * A robot takes part in a round only while the tracker can see it, and one that blinks out
   * for a single round used to drop the slot count by one. That is not a small change: a ring
   * of five slots puts every one of them at a different bearing from a ring of six, so the
   * whole fleet is reassigned, drives towards the new layout, and drives back the moment the
   * robot reappears. With a tracker that loses a robot every few rounds the fleet never
   * settles at all -- it holds roughly the right shape while twitching around it forever.
   *
   * Carrying the robot's last known offset forward keeps the slot count fixed and its slot
   * reserved, so it drops straight back into place. A robot that has genuinely left is
   * forgotten after `ParticipantMemoryRounds` and the shape closes up without it.
   *
   * Only the root evaluates this, inside `plan`'s `branch`, so the `rep` state lives on
   * exactly one device.
   */
  private def steadyParticipants(reached: Map[Int, (Double, Double)]): List[(Int, (Double, Double))] =
    val remembered = rep(Map.empty[Int, ((Double, Double), Int)]) { previous =>
      val stillMissed = previous.collect {
        case (id, (offset, rounds)) if !reached.contains(id) && rounds + 1 < ShapeFormation.ParticipantMemoryRounds =>
          id -> (offset, rounds + 1)
      }
      stillMissed ++ reached.view.mapValues(offset => (offset, 0)).toMap
    }
    orderedNodes(remembered.view.mapValues(_._1).toMap)

  /**
   * The optimal assignment, held steady across rounds.
   *
   * A collinear slot set -- a line, a column -- admits many matchings of the same cost, so
   * the optimum jumps to a different permutation on the slightest movement. Each jump makes
   * two robots swap slots and drive past each other, which moves them, which flips the
   * optimum again: the fleet ends up in a limit cycle and never settles. So a fresh
   * matching is adopted only when it is better than the one already in hand by a margin.
   *
   * Only the root evaluates this, inside `plan`'s `branch`, so the `rep` state lives on
   * exactly one device.
   */
  private def steadyAssignment(
      robots: List[(Int, (Double, Double))],
      targets: List[(Double, Double)]
  ): Map[Int, (Double, Double)] =
    val held = rep(Map.empty[Int, Int]) { previous =>
      val fresh = AssignmentSolver.solveIndices(robots, targets)
      val reusable = previous.nonEmpty
        && previous.keySet == robots.iterator.map(_._1).toSet
        && previous.values.forall(targets.indices.contains)
        && previous.values.toSet.size == targets.size
      if !reusable then fresh
      else
        val heldCost = AssignmentSolver.cost(robots, targets, previous)
        val freshCost = AssignmentSolver.cost(robots, targets, fresh)
        if freshCost < heldCost - ShapeFormation.AssignmentSwitchMargin then fresh else previous
    }
    AssignmentSolver.displacements(robots, targets, held)

  /**
   * Drives towards the assigned slot while being pushed away from close neighbours.
   *
   * @param displacement vector from this robot to its slot
   */
  private def actuate(frame: AnchorFrame, displacement: (Double, Double)): Actuation =
    val stabilityThreshold = sense[Double](BaseDemo.StabilityThreshold)
    val collisionArea = sense[Double](BaseDemo.CollisionArea)
    val distanceTowardGoal = module(displacement)
    val neighborMap = neighbourVectors
      .map { (id, nbrVector) => id -> Point3D(nbrVector._1, nbrVector._2, 0.0) }
    // The marker yaw is not the direction the robot faces; DifferentialDrive owns that conversion.
    val (referenceX, referenceY) =
      DifferentialDrive.headingVector(frame.reference, DriveConfig.fromEnvironment)
    // Aggregate repulsion from all neighbors within collisionRange (inverse-square weighting)
    val repulsionSum = computeRepulsionSum(neighborMap, collisionArea)
    val avoidance = if repulsionSum.magnitude > maxRepulsion then repulsionSum.normalize * maxRepulsion else repulsionSum

    // Heading blends the goal with the repulsion push; how far to travel stays the pure goal distance,
    // so that collision avoidance cannot inflate the speed the controller picks.
    val heading = (Point3D(displacement._1, displacement._2, 0) + avoidance).normalize
    if distanceTowardGoal < stabilityThreshold then
      // The anchor stands at the origin of its own formation, so it has nowhere to go.
      if frame.isRoot then NoOp
      else computeGoalConsideringAvoidance((referenceX, referenceY), avoidance, collisionArea)
    else
      Forward((heading.x, heading.y), distanceTowardGoal)

  /**
   * Phase of the time-varying shapes, derived directly from the shared clock rather than
   * from a counter: every device reads the same instant, and the value survives the loss of
   * a robot's `rep` state.
   */
  protected def phase: Double =
    ShapeFormation.phaseAt(timestamp(), sense[Double](ShapeFormation.WavePeriod))

  protected def orderedNodes(nodes: Map[Int, (Double, Double)]): List[(Int, (Double, Double))] =
    nodes.toList.sortBy(_._1)

  private def computeRepulsionSum(neighborMap: Map[Int, Point3D], collisionArea: Double): Point3D =
    neighborMap.values
      .map { p =>
        val d = p.magnitude
        if d < 1e-9 || d >= collisionArea then Point3D.Zero
        else
          val proximity = math.max(0.0, 1.0 - d / collisionArea) // 0..1
          val weight = repulsionStrength * proximity / (d * d) // stronger when closer
          (p.normalize * weight) * -1.0
      }
      .foldLeft(Point3D.Zero)(_ + _)

  private def computeGoalConsideringAvoidance(
    reference: (Double, Double),
    avoidance: Point3D,
    collisionArea: Double
  ): Actuation =
    if avoidance.magnitude > 0.01 then
      val combinedVector = (Point3D(reference._1, reference._2, 0) + avoidance).normalize
      // Already within the stability threshold, so the goal distance is meaningless here and what
      // the robot wants to travel is the repulsion push. That push is an inverse-square *strength*,
      // not a length, so convert it: at full strength back off by the whole collision radius, and
      // proportionally less below that. Handing the raw magnitude to a controller that reads metres
      // made a strength of 1.5 mean "1.5 m to go, drive flat out".
      val escapeDistance = math.min(1.0, avoidance.magnitude / maxRepulsion) * collisionArea
      Forward((combinedVector.x, combinedVector.y), escapeDistance)
    else
      Rotation(reference._1, reference._2)

  /**
   * Finds the globally optimal one-to-one matching between robots and target slots
   * that minimizes the sum of squared distances, preventing path crossings and minimizing total movement.
   */
  protected def optimalAssignment(
    robots: List[(Int, (Double, Double))],
    targets: List[(Double, Double)]
  ): Map[Int, (Double, Double)] =
    AssignmentSolver.solve(robots, targets)

  /**
   * Rotates a list of target coordinates by a given angle in radians.
   */
  protected def rotateTargets(
    targets: List[(Double, Double)],
    angle: Double
  ): List[(Double, Double)] =
    ShapeFormation.rotated(targets, angle)

object ShapeFormation:
  /** Seconds for one full cycle of a time-varying shape. */
  val WavePeriod = "wavePeriod"

  /** How far, in metres, a time-varying shape departs from its rest position. */
  val WaveAmplitude = "waveAmplitude"

  /** How many crests fit across a travelling wave. */
  val WaveNumber = "waveNumber"

  val DEFAULTS: Map[String, Double] = Map(
    WavePeriod -> 6.0,
    WaveAmplitude -> 0.2,
    WaveNumber -> 1.0
  )

  /** Phase of a `periodSeconds`-long cycle at the given shared-clock reading. */
  def phaseAt(timestampMillis: Long, periodSeconds: Double): Double =
    if periodSeconds <= 0.0 then 0.0
    else 2 * math.Pi * (timestampMillis / 1000.0) / periodSeconds

  /**
   * `count` evenly spaced points on a ring, where each point's radius may depend on its
   * index. Shared by the static circle and by all the time-varying rings.
   */
  def ring(count: Int, bearingOffset: Double)(radiusAt: Int => Double): List[(Double, Double)] =
    if count <= 0 then List.empty
    else
      val division = (math.Pi * 2) / count
      (0 until count).map { i =>
        val angle = division * i + bearingOffset
        val r = radiusAt(i)
        (math.sin(angle) * r, math.cos(angle) * r)
      }.toList

  /**
   * Signed positions along a straight line through the origin, ordered from one end to the
   * other. The origin itself is skipped: the anchor robot stands there.
   */
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
    targets.map { (x, y) =>
      (x * cosTheta - y * sinTheta, x * sinTheta + y * cosTheta)
    }

  /** Radius floor, so a pulsing ring can never invert through its own centre. */
  val MinRadius: Double = 0.05

  /**
   * How many rounds a robot the collect has stopped reaching keeps its slot before the shape
   * is re-laid without it. Long enough to ride out the gaps a tracker leaves, short enough
   * that a robot which has genuinely left is not held a slot for long.
   */
  val ParticipantMemoryRounds: Int = 10

  /**
   * How much better, in total squared metres, a fresh assignment must be before the fleet
   * abandons the one it is already executing. Large enough to ignore the ties a symmetric
   * shape produces, small enough that a genuinely better matching is still taken up.
   */
  val AssignmentSwitchMargin: Double = 0.05

class LineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    ShapeFormation.lineOffsets(ctx.count, distanceThreshold).map(x => (x, 0.0))

  private def distanceThreshold: Double = sense(LineFormation.INTER_DISTANCE_SENSING)

object LineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceLine"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)

class CircleFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    ShapeFormation.ring(ctx.count, 0.0)(_ => radius)

  private def radius: Double = sense(CircleFormation.RADIUS_SENSING)

object CircleFormation:
  val RADIUS_SENSING: String = "radius"
  val DEFAULTS: Map[String, Double] = Map(RADIUS_SENSING -> 0.6)

class SquareFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    SquareFormation.grid(ctx.count, distanceBetweenNodes)

  private def distanceBetweenNodes: Double = sense(SquareFormation.INTER_DISTANCE_SENSING)

object SquareFormation:
  val INTER_DISTANCE_SENSING = "interDistanceSquare"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4)

  /**
   * A square grid of `count` cells with the given spacing. The centre cell is skipped -- the
   * anchor robot occupies it -- and the grid grows by one to compensate.
   */
  def grid(count: Int, spacing: Double): List[(Double, Double)] =
    if count <= 0 then List.empty
    else
      val side = math.ceil(math.sqrt(count + 1)).toInt
      (for
        y <- 0 until side
        x <- 0 until side if !(x == 0 && y == 0)
      yield (x * spacing, y * spacing)).take(count).toList

class VFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    if ctx.count == 0 then return List.empty
    val leftCount = ctx.count / 2
    val rightCount = ctx.count - leftCount
    val dx = distanceBetweenNodes * math.cos(armAngle)
    val dy = distanceBetweenNodes * math.sin(armAngle)

    val targetsLeft = (1 to leftCount).map(k => (-k * dx, k * dy))
    val targetsRight = (1 to rightCount).map(k => (k * dx, k * dy))
    val targets = (targetsLeft ++ targetsRight).toList

    // Rotate the targets so that the apex of the V points along the formation's reference
    // heading. This must be the shared reference rather than `orientation`, which is this
    // robot's own heading and would give a differently tilted V on every device.
    rotateTargets(targets, ctx.reference)

  private def distanceBetweenNodes: Double = sense(VFormation.INTER_DISTANCE_SENSING)
  private def armAngle: Double = sense(VFormation.ANGLE_SENSING)

object VFormation:
  val INTER_DISTANCE_SENSING = "interDistanceV"
  val ANGLE_SENSING = "angleV"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4, ANGLE_SENSING -> - Math.PI / 4)

class VerticalLineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    if ctx.count == 0 then return List.empty
    (0 until ctx.count).map { index =>
      (0.0, - (index + 1) * distanceBetweenNodes)
    }.toList

  private def distanceBetweenNodes: Double = sense(VerticalLineFormation.INTER_DISTANCE_SENSING)

object VerticalLineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceVertical"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)

class HeartFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    HeartFormation.curve(ctx.count, scale)

  private def scale: Double = sense(HeartFormation.SCALE_SENSING)

object HeartFormation:
  val SCALE_SENSING = "scaleHeart"
  val DEFAULTS = Map(SCALE_SENSING -> 0.06)

  /**
   * `count` points along the parametric heart curve, scaled and shifted so the bottom cusp
   * sits at the origin. The cusp itself is left out, since the anchor robot occupies it.
   */
  def curve(count: Int, scale: Double): List[(Double, Double)] =
    if count <= 0 then List.empty
    else
      val span = count + 1
      (0 until count).map { i =>
        val t = -math.Pi + (2.0 * math.Pi * (i + 1).toDouble) / span.toDouble
        val sinT = math.sin(t)
        val x = 16 * sinT * sinT * sinT
        val y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        (x * scale, (y + 17.0) * scale)
      }.toList

/** A ring that turns: the slots keep their spacing but their bearing advances with time. */
class OrbitFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val r = sense[Double](CircleFormation.RADIUS_SENSING)
    ShapeFormation.ring(ctx.count, ctx.phase)(_ => r)

/** A ring that breathes: every slot's radius pulses in unison. */
class BreathingCircleFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val rest = sense[Double](CircleFormation.RADIUS_SENSING)
    val amplitude = sense[Double](ShapeFormation.WaveAmplitude)
    val r = math.max(ShapeFormation.MinRadius, rest + amplitude * math.sin(ctx.phase))
    ShapeFormation.ring(ctx.count, 0.0)(_ => r)

/**
 * A travelling wave over the ring: each slot's radius is offset by
 * `amplitude * sin(phase - 2*Pi*waveNumber*i/count)`, so the crest runs around the
 * formation instead of every robot moving together.
 */
class RingWaveFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val rest = sense[Double](CircleFormation.RADIUS_SENSING)
    val amplitude = sense[Double](ShapeFormation.WaveAmplitude)
    val waveNumber = sense[Double](ShapeFormation.WaveNumber)
    ShapeFormation.ring(ctx.count, 0.0) { i =>
      val spatial = 2 * math.Pi * waveNumber * i / ctx.count
      math.max(ShapeFormation.MinRadius, rest + amplitude * math.sin(ctx.phase - spatial))
    }

/**
 * A sine wave standing on a line: the robots hold their spacing along the axis and ride the
 * wave across it, so the crest travels down the line as the cycle advances.
 */
class SineLineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    SineLineFormation.wave(
      ctx.count,
      sense[Double](LineFormation.INTER_DISTANCE_SENSING),
      sense[Double](ShapeFormation.WaveAmplitude),
      sense[Double](ShapeFormation.WaveNumber),
      ctx.phase
    )

object SineLineFormation:
  /**
   * Slots along the x axis at `spacing`, displaced on y by
   * `amplitude * sin(phase - 2*Pi*waveNumber*x/span)` where `span` is the length of the
   * line. So `waveNumber` crests fit across the fleet, and they travel with `phase`.
   */
  def wave(
      count: Int,
      spacing: Double,
      amplitude: Double,
      waveNumber: Double,
      phase: Double
  ): List[(Double, Double)] =
    val span = math.max(spacing * count, 1e-9)
    ShapeFormation.lineOffsets(count, spacing).map { x =>
      val spatial = 2 * math.Pi * waveNumber * x / span
      (x, amplitude * math.sin(phase - spatial))
    }
