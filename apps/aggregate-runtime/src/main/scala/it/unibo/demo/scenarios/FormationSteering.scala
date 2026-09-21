package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation}
import it.unibo.demo.robot.{DifferentialDrive, DriveConfig}
import it.unibo.scafi.space.Point3D
import it.unibo.scafi.space.optimization.RichPoint3D

/** Drives towards the assigned slot while being pushed away from close neighbours. */
trait FormationSteering extends BaseDemo:
  import FormationSteering.*

  /** @param displacement vector from this robot to its slot */
  private[scenarios] def actuate(frame: AnchorFrame, displacement: (Double, Double)): Actuation =
    val collisionArea = sense[Double](BaseDemo.CollisionArea)
    val distanceToGoal = module(displacement)
    val travelling = distanceToGoal >= sense[Double](BaseDemo.StabilityThreshold)
    val neighbours = neighbourVectors.map((id, vector) => id -> Point3D(vector._1, vector._2, 0.0))
    val avoidance = capped(repulsion(neighbours, collisionArea))
    // Kept outside the branches: every device must run this `rep` to stay aligned.
    val tangent = steadyDetour(displacement, neighbours, collisionArea, !frame.isRoot && travelling)
    if travelling then approach(displacement, avoidance + tangent)
    // The anchor stands at the origin of its own formation, so it has nowhere to go.
    else if frame.isRoot then NoOp
    else hold(frame.reference, avoidance, collisionArea)

  /** Steering only: the correction bends the heading without adding metres to travel. */
  private def approach(displacement: (Double, Double), correction: Point3D): Actuation =
    val heading = (Point3D(displacement._1, displacement._2, 0) + correction).normalize
    Forward((heading.x, heading.y), module(displacement))

  /** In place: turn to the shared heading, unless a neighbour is close enough to back away from. */
  private def hold(reference: Double, avoidance: Point3D, collisionArea: Double): Actuation =
    // The marker yaw is not the direction the robot faces; DifferentialDrive owns that conversion.
    val (referenceX, referenceY) = DifferentialDrive.headingVector(reference, DriveConfig.fromEnvironment)
    if avoidance.magnitude <= HoldingAvoidance then Rotation(referenceX, referenceY)
    else
      val direction = (Point3D(referenceX, referenceY, 0) + avoidance).normalize
      // Repulsion is an inverse-square strength, not a length: at most one collision radius.
      val escape = math.min(1.0, avoidance.magnitude / MaxRepulsion) * collisionArea
      Forward((direction.x, direction.y), escape)

  private def repulsion(neighbours: Map[Int, Point3D], collisionArea: Double): Point3D =
    neighbours.values
      .map(p => p.normalize * -weight(p.magnitude, collisionArea))
      .foldLeft(Point3D.Zero)(_ + _)

  private def capped(force: Point3D): Point3D =
    if force.magnitude > MaxRepulsion then force.normalize * MaxRepulsion else force

  /**
   * Commit to one side of the neighbour blocking the slot: a purely radial field cancels out
   * against a collinear obstacle, and a remembered tangent still gives the robot somewhere to go.
   * Only the choice is remembered -- geometry and radial repulsion still come from the field.
   */
  private def steadyDetour(
      displacement: (Double, Double),
      neighbours: Map[Int, Point3D],
      collisionArea: Double,
      enabled: Boolean
  ): Point3D =
    val goal = Point3D(displacement._1, displacement._2, 0)
    val radius = collisionArea * DetourRadiusFactor
    val held = rep(Option.empty[Detour]) { previous =>
      if !enabled || collisionArea <= 0.0 then None
      else
        retained(previous, neighbours, goal, radius)
          .orElse(chosen(neighbours, goal, radius, collisionArea))
    }
    // A missed observation preserves the choice briefly, never the force or an old position.
    held
      .flatMap(detour => neighbours.get(detour.neighbor).map(p => (detour.side, p)))
      .map((side, p) => clockwise(p) * (side * math.min(MaxRepulsion, weight(p.magnitude, radius))))
      .getOrElse(Point3D.Zero)

  private def retained(
      previous: Option[Detour],
      neighbours: Map[Int, Point3D],
      goal: Point3D,
      radius: Double
  ): Option[Detour] =
    previous.flatMap { detour =>
      val obstructed =
        neighbours.get(detour.neighbor).exists(p => p.magnitude < radius && blocks(p, goal, radius))
      val clearRounds = if obstructed then 0 else detour.clearRounds + 1
      Option.when(clearRounds < DetourClearRounds)(detour.copy(clearRounds = clearRounds))
    }

  private def chosen(
      neighbours: Map[Int, Point3D],
      goal: Point3D,
      radius: Double,
      clearance: Double
  ): Option[Detour] =
    neighbours.toList
      .filter((_, p) => p.magnitude > Epsilon && p.magnitude < radius && blocks(p, goal, clearance))
      .minByOption((id, p) => (p.magnitude, id))
      .map { (id, p) =>
        val direction = goal.normalize
        val tangent = clockwise(p)
        val progress = tangent.x * direction.x + tangent.y * direction.y
        Detour(id, if progress < -Epsilon then -1.0 else 1.0)
      }

object FormationSteering:
  private case class Detour(neighbor: Int, side: Double, clearRounds: Int = 0)

  private val Epsilon = 1e-9
  private val RepulsionStrength = 0.6
  private val MaxRepulsion = 2.0
  private val HoldingAvoidance = 0.01
  private val DetourRadiusFactor = 1.15
  private val DetourClearRounds = 3

  extension (p: Point3D)
    def magnitude: Double = p.distance(Point3D.Zero)
    def normalize: Point3D =
      val m = p.magnitude
      if m < Epsilon then Point3D.Zero else Point3D(p.x / m, p.y / m, 0)

  /** Inverse-square, fading to nothing at `radius`. */
  private def weight(distance: Double, radius: Double): Double =
    if distance < Epsilon || distance >= radius then 0.0
    else RepulsionStrength * math.max(0.0, 1.0 - distance / radius) / (distance * distance)

  /** True while `p` sits within `clearance` of the path to the goal, ahead of the robot. */
  private def blocks(p: Point3D, goal: Point3D, clearance: Double): Boolean =
    val direction = goal.normalize
    val along = p.x * direction.x + p.y * direction.y
    along > 0.0 && p.distance(direction * math.min(along, goal.magnitude)) < clearance

  /** The same tie-break on both devices, so a frontal pair passes on opposite sides. */
  private def clockwise(p: Point3D): Point3D = Point3D(-p.y, p.x, 0).normalize
