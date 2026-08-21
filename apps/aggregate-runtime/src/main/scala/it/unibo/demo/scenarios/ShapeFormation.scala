package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation, Stop}
import it.unibo.scafi.space.Point3D
import it.unibo.scafi.space.optimization.RichPoint3D
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
    val leaderSelected = sense[Int]("leader")
    val stabilityThreshold = sense[Double]("stabilityThreshold")
    val collisionArea = sense[Double]("collisionArea")
    val leader = mid() == leaderSelected
    val potential = gradientCast(leader, 0.0, _ + nbrRange())
    val directionTowardsLeader =
      gradientCast(leader, (0.0, 0.0), (x, y) => (x + distanceVector._1, y + distanceVector._2))
    val leaderOrientation = gradientCast(leader, sense[Double]("orientation"), identity)
    val collectInfo =
      collectCast[Map[Int, (Double, Double)]](potential, _ ++ _, Map(mid() -> directionTowardsLeader), Map.empty)
        .filter(_._1 != mid())
    val ordered = orderedNodes(collectInfo.toSet)
    val suggestion = branch(leaderSelected == mid())(calculateSuggestion(ordered))(Map.empty)
    val local = gradientCast(leader, suggestion, a => a).getOrElse(mid(), (0.0, 0.0))
    val distanceTowardGoal = Math.sqrt(local._1 * local._1 + local._2 * local._2)
    val neighborMap = foldhoodPlus[Map[Int, (Double, Double)]](Map.empty)((a, b) => a ++ b)(Map(nbr(mid()) -> distanceVector))
      .map { (id, nbrVector) => id -> Point3D(nbrVector._1, nbrVector._2, 0.0) }
    // convert the orientation to a 2d vector
    val (orientationLeaderX, orientationLeaderY) = (-math.sin(leaderOrientation), math.cos(leaderOrientation))
    // Aggregate repulsion from all neighbors within collisionRange (inverse-square weighting)
    val repulsionSum = computeRepulsionSum(neighborMap, collisionArea)
    val avoidance = if repulsionSum.magnitude > maxRepulsion then repulsionSum.normalize * maxRepulsion else repulsionSum

    // Heading blends the goal with the repulsion push; how far to travel stays the pure goal distance,
    // so that collision avoidance cannot inflate the speed the controller picks.
    val heading = (Point3D(local._1, local._2, 0) + avoidance).normalize
    val res =
      if distanceTowardGoal < stabilityThreshold then
        if leader then NoOp else computeGoalConsideringAvoidance((orientationLeaderX, orientationLeaderY), avoidance)
      else
        Forward((heading.x, heading.y), distanceTowardGoal)
    res

  protected def orderedNodes(nodes: Set[(Int, (Double, Double))]): List[(Int, (Double, Double))] =
    nodes.filter(_._1 != mid()).toList.sortBy(_._1)

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

  private def computeGoalConsideringAvoidance(leaderOrientation: (Double, Double), avoidance: Point3D): Actuation =
    if avoidance.magnitude > 0.01 then
      val combinedVector = (Point3D(leaderOrientation._1, leaderOrientation._2, 0) + avoidance).normalize
      // Already within the stability threshold, so the goal distance is meaningless here:
      // what the robot actually wants to travel is the repulsion push itself.
      Forward((combinedVector.x, combinedVector.y), avoidance.magnitude)
    else
      Rotation(leaderOrientation._1, leaderOrientation._2)


  def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)]

  /**
   * Finds the globally optimal one-to-one matching between robots and target slots
   * that minimizes the sum of squared distances, preventing path crossings and minimizing total movement.
   * Uses an optimized Branch and Bound algorithm.
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
    val cosTheta = math.cos(angle)
    val sinTheta = math.sin(angle)
    targets.map { (x, y) =>
      val rx = x * cosTheta - y * sinTheta
      val ry = x * sinTheta + y * cosTheta
      (rx, ry)
    }

class LineFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val n = ordered.size
    val leftCount = n / 2
    val rightCount = n - leftCount
    val leftTargets = (0 until leftCount).map(index => (-(index + 1) * distanceThreshold, 0.0))
    val rightTargets = (0 until rightCount).map(index => ((index + 1) * distanceThreshold, 0.0))
    val targets = (leftTargets ++ rightTargets).toList
    optimalAssignment(ordered, targets)

  private def distanceThreshold: Double = sense(LineFormation.INTER_DISTANCE_SENSING)

object LineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceLine"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)

class CircleFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val division = (math.Pi * 2) / ordered.size
    val targets = ordered.indices.map { i =>
      val angle = division * (i + 1)
      (math.sin(angle) * radius, math.cos(angle) * radius)
    }.toList
    optimalAssignment(ordered, targets)

  private def radius: Double = sense(CircleFormation.RADIUS_SENSING)

object CircleFormation:
  val RADIUS_SENSING: String = "radius"
  val DEFAULTS: Map[String, Double] = Map(RADIUS_SENSING -> 0.6)

class SquareFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val n = ordered.size
    val side = math.ceil(math.sqrt(n + 1)).toInt
    val gridCoords = (for
      y <- 0 until side
      x <- 0 until side if !(x == 0 && y == 0)
    yield (x, y)).take(n)
    val targets = gridCoords.map { (gx, gy) =>
      (gx * distanceBetweenNodes, gy * distanceBetweenNodes)
    }.toList
    optimalAssignment(ordered, targets)

  private def distanceBetweenNodes: Double = sense(SquareFormation.INTER_DISTANCE_SENSING)

object SquareFormation:
  val INTER_DISTANCE_SENSING = "interDistanceSquare"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4)

class VFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val n = ordered.size
    val leftCount = n / 2
    val rightCount = n - leftCount
    val dx = distanceBetweenNodes * math.cos(armAngle)
    val dy = distanceBetweenNodes * math.sin(armAngle)

    val targetsLeft = (1 to leftCount).map(k => (-k * dx, k * dy))
    val targetsRight = (1 to rightCount).map(k => (k * dx, k * dy))
    val targets = (targetsLeft ++ targetsRight).toList

    // Rotate the targets so that the apex of the V points in the leader's current orientation
    val leaderAngle = sense[Double]("orientation")
    val rotatedTargets = rotateTargets(targets, leaderAngle)

    optimalAssignment(ordered, rotatedTargets)

  private def distanceBetweenNodes: Double = sense(VFormation.INTER_DISTANCE_SENSING)
  private def armAngle: Double = sense(VFormation.ANGLE_SENSING)

object VFormation:
  val INTER_DISTANCE_SENSING = "interDistanceV"
  val ANGLE_SENSING = "angleV"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4, ANGLE_SENSING -> - Math.PI / 4)

class VerticalLineFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val targets = ordered.indices.map { index =>
      (0.0, - (index + 1) * distanceBetweenNodes)
    }.toList
    optimalAssignment(ordered, targets)

  private def distanceBetweenNodes: Double = sense(VerticalLineFormation.INTER_DISTANCE_SENSING)

object VerticalLineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceVertical"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)

class HeartFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val n = ordered.size
    val s = scale
    // Distribute targets along the heart curve, excluding the bottom cusp (t = -Pi/Pi) which is occupied by the leader
    val targets = ordered.indices.map { i =>
      val t = -math.Pi + (2.0 * math.Pi * (i + 1).toDouble) / (n.toDouble + 1.0)
      val sinT = math.sin(t)
      val x = 16 * sinT * sinT * sinT
      val y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)

      // Scale and shift so that the bottom cusp (at t = -Pi, where raw y = -17) is at (0, 0)
      (x * s, (y + 17.0) * s)
    }.toList
    optimalAssignment(ordered, targets)

  private def scale: Double = sense(HeartFormation.SCALE_SENSING)

object HeartFormation:
  val SCALE_SENSING = "scaleHeart"
  val DEFAULTS = Map(SCALE_SENSING -> 0.06)
