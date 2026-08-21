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
    val potential = gradientCast(leader, 0.0, _ + nbrRange)
    val directionTowardsLeader =
      gradientCast(leader, (0.0, 0.0), (x, y) => (x + distanceVector._1, y + distanceVector._2))
    val leaderOrientation = gradientCast(leader, sense[Double]("orientation"), identity)
    val collectInfo =
      collectCast[Map[Int, (Double, Double)]](potential, _ ++ _, Map(mid() -> directionTowardsLeader), Map.empty)
        .filter(_._1 != mid())
    val ordered = orderedNodes(collectInfo.toSet)
    val suggestion = branch(leaderSelected == mid())(calculateSuggestion(ordered))(Map.empty)
    val local = gradientCast(leader, suggestion, a => a).getOrElse(mid, (0.0, 0.0))
    val distanceTowardGoal = Math.sqrt(local._1 * local._1 + local._2 * local._2)
    val neighborMap = foldhoodPlus[Map[Int, (Double, Double)]](Map.empty)((a, b) => a ++ b)(Map(nbr(mid) -> distanceVector))
      .map { (id, nbrVector) => id -> Point3D(nbrVector._1, nbrVector._2, 0.0) }
    // convert the orientation to a 2d vector
    val (orientationLeaderX, orientationLeaderY) = (-math.sin(leaderOrientation), math.cos(leaderOrientation))
    // Aggregate repulsion from all neighbors within collisionRange (inverse-square weighting)
    val repulsionSum = computeRepulsionSum(neighborMap, collisionArea)
    val avoidance = if repulsionSum.magnitude > maxRepulsion then repulsionSum.normalize * maxRepulsion else repulsionSum

    val resultingVector = ((Point3D(local._1, local._2, 0))  + avoidance).normalize
    val res =
      if distanceTowardGoal < stabilityThreshold then
        if leader then NoOp else computeGoalConsideringAvoidance((orientationLeaderX, orientationLeaderY), avoidance)
      else
        Forward((resultingVector.x, resultingVector.y))
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
      Forward((combinedVector.x, combinedVector.y))
    else
      Rotation(leaderOrientation._1, leaderOrientation._2)


  def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)]

class LineFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    val (leftSlots, rightSlots) = ordered.indices.splitAt(ordered.size / 2)
    var devicesAvailable = ordered
    val leftCandidates = leftSlots
      .map: index =>
        val candidate = devicesAvailable
          .map:
            case (id, (xPos, yPos)) =>
              val newPos @ (newXpos, newYpos) = ((-(index + 1) * distanceThreshold) + xPos, yPos)
              val modulo = math.sqrt((newXpos * newXpos) + (newYpos * newYpos))
              (id, modulo, newPos)
          .minBy(_._2)
        devicesAvailable = devicesAvailable.filterNot(_._1 == candidate._1)
        candidate._1 -> candidate._3
      .toMap
    val rightCandidates = rightSlots
      .map(i => i - rightSlots.min)
      .map: index =>
        val candidate = devicesAvailable
          .map:
            case (id, (xPos, yPos)) =>
              val newPos @ (newXpos, newYpos) = (((index + 1) * distanceThreshold) + xPos, yPos)
              val modulo = math.sqrt((newXpos * newXpos) + (newYpos * newYpos))
              (id, modulo, newPos)
          .minBy(_._2)
        devicesAvailable = devicesAvailable.filterNot(_._1 == candidate._1)
        candidate._1 -> candidate._3
      .toMap
    leftCandidates ++ rightCandidates

  private def distanceThreshold: Double = sense(LineFormation.INTER_DISTANCE_SENSING)

object LineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceLine"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)

class CircleFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    val division = (math.Pi * 2) / ordered.size
    val precomputedAngels = ordered.indices.map(i => division * (i + 1))
    var availableDevices = ordered
    precomputedAngels
      .map: angle =>
        val candidate = availableDevices
          .map:
            case (id, (xPos, yPos)) =>
              val newPos @ (newXpos, newYpos) = (math.sin(angle) * radius + xPos, math.cos(angle) * radius + yPos)
              (id, math.sqrt((newXpos * newXpos) + (newYpos * newYpos)), newPos)
          .minBy(_._2)
        availableDevices = removeDeviceFromId(candidate._1, availableDevices)
        candidate._1 -> candidate._3
      .toMap

  private def radius: Double = sense(CircleFormation.RADIUS_SENSING)

  private def removeDeviceFromId(id: Int, devices: List[(Int, (Double, Double))]): List[(Int, (Double, Double))] =
    devices.filterNot: (currentId, _) =>
      currentId == id

  private def nearestFromPoint(point: (Double, Double), devices: List[(Int, (Double, Double))]): Int =
    devices
      .map:
        case (id, (xPos, yPos)) =>
          val xDelta = math.abs(point._1 - xPos)
          val yDelta = math.abs(point._2 - yPos)
          id -> math.sqrt((xDelta * xDelta) + (yDelta * yDelta))
      .minBy(_._1)
      ._1

object CircleFormation:
  val RADIUS_SENSING: String = "radius"
  val DEFAULTS: Map[String, Double] = Map(RADIUS_SENSING -> 0.6)

class SquareFormation extends ShapeFormation():
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val n = ordered.size
    // side length (number of points per side) to accommodate all nodes + leader
    val side = math.ceil(math.sqrt(n + 1)).toInt
    // Generate grid coordinates excluding leader position (0,0)
    val gridCoords = (for
      y <- 0 until side
      x <- 0 until side if !(x == 0 && y == 0)
    yield (x, y)).take(n)
    var available = ordered
    gridCoords
      .map: (gx, gy) =>
        val candidate = available
          .map:
            case (id, (xPos, yPos)) =>
              // target absolute vector from leader for this grid cell
              val targetX = gx * distanceBetweenNodes
              val targetY = gy * distanceBetweenNodes
              // Following existing pattern, combine with current vector (acts like bias towards current pos)
              val newPos @ (newXpos, newYpos) = (targetX + xPos, targetY + yPos)
              (id, math.sqrt(newXpos * newXpos + newYpos * newYpos), newPos)
          .minBy(_._2)
        available = available.filterNot(_._1 == candidate._1)
        candidate._1 -> candidate._3
      .toMap

  private def distanceBetweenNodes: Double = sense(SquareFormation.INTER_DISTANCE_SENSING)
object SquareFormation:
  val INTER_DISTANCE_SENSING = "interDistanceSquare"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4)

class VFormation extends ShapeFormation():
  // armAngle: angle (in radians) of each arm relative to the x-axis (default suggestion: math.Pi/4)
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    val n = ordered.size
    val leftCount = n / 2
    val rightCount = n - leftCount
    val dx = distanceBetweenNodes * math.cos(armAngle)
    val dy = distanceBetweenNodes * math.sin(armAngle)

    // Targets: leader apex assumed at (0,0) (leader itself not in ordered list)
    val targetsLeft = (1 to leftCount).map(k => (-k * dx, k * dy))
    val targetsRight = (1 to rightCount).map(k => (k * dx, k * dy))
    val targets = targetsLeft ++ targetsRight

    var available = ordered
    targets
      .map: (tx, ty) =>
        val candidate = available
          .map:
            case (id, (xPos, yPos)) =>
              val newPos @ (newX, newY) = (tx + xPos, ty + yPos)
              (id, math.sqrt(newX * newX + newY * newY), newPos)
          .minBy(_._2)
        available = available.filterNot(_._1 == candidate._1)
        candidate._1 -> candidate._3
      .toMap

  private def distanceBetweenNodes: Double = sense(VFormation.INTER_DISTANCE_SENSING)
  private def armAngle: Double = sense(VFormation.ANGLE_SENSING)
object VFormation:
  val INTER_DISTANCE_SENSING = "interDistanceV"
  val ANGLE_SENSING = "angleV"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4, ANGLE_SENSING -> - Math.PI / 4)

class VerticalLineFormation extends ShapeFormation():
  // Leader at top (0,0). Other robots placed below along -Y axis at multiples of distanceThreshold.
  override def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)] =
    if ordered.isEmpty then return Map.empty
    var available = ordered
    ordered.indices
      .map: index =>
        val candidate = available
          .map:
            case (id, (xPos, yPos)) =>
              val offsetY = - (index + 1) * distanceBetweenNodes
              val newPos @ (newX, newY) = (xPos, yPos + offsetY) // shift downward
              (id, math.sqrt(newX * newX + newY * newY), newPos)
          .minBy(_._2)
        available = available.filterNot(_._1 == candidate._1)
        candidate._1 -> candidate._3
      .toMap

  private def distanceBetweenNodes: Double = sense(VerticalLineFormation.INTER_DISTANCE_SENSING)
object VerticalLineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceVertical"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)
