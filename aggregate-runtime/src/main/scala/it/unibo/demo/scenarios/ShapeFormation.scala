package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, Rotation}
import it.unibo.scafi.space.Point3D
import it.unibo.scafi.space.optimization.RichPoint3D
abstract class ShapeFormation(leaderSelected: Int, stabilityThreshold: Double, collisionRange: Double) extends BaseDemo:
  extension(p: Point3D)
    def magnitude: Double = p.distance(Point3D.Zero)
    def normalize: Point3D = Point3D(p.x / p.magnitude, p.y / p.magnitude, 0)
  def calculateSuggestion(ordered: List[(Int, (Double, Double))]): Map[Int, (Double, Double)]
  val repulsionStrength = 50.0
  override def main(): Actuation =
    val leader = mid() == leaderSelected
    val potential = gradientCast(leader, 0.0, _ + nbrRange)
    val directionTowardsLeader =
      gradientCast(leader, (0.0, 0.0), (x, y) => (x + distanceVector._1, y + distanceVector._2))
    val collectInfo =
      collectCast[Map[Int, (Double, Double)]](potential, _ ++ _, Map(mid() -> directionTowardsLeader), Map.empty)
        .filter(_._1 != mid())
    val ordered = orderedNodes(collectInfo.toSet)
    val suggestion = branch(leaderSelected == mid())(calculateSuggestion(ordered))(Map.empty)
    val local = gradientCast(leader, suggestion, a => a).getOrElse(mid, (0.0, 0.0))
    val distanceTowardGoal = Math.sqrt(local._1 * local._1 + local._2 * local._2)
    val neighborsInCollisionArea = foldhoodPlus[Map[Int, (Double, Double)]](Map.empty)((a, b) => a ++ b)(Map(nbr(mid) -> distanceVector))
      .map { (id, nbrVector) => id -> Point3D(nbrVector._1, nbrVector._2, 0.0) }
      .map { case (id, nbrVector) => id -> (nbrVector, nbrVector.distance(Point3D.Zero))}
      .filter { case (id, (_, distance)) => distance < collisionRange }
      .map { case (id, (vector, distance)) => id -> (vector.normalize * (repulsionStrength / (distance * distance)), distance) }
      .toList
      .sortBy(_._2._2)
      .map(_._2._1)
      //.view.mapValues(_._1).values
    val obstacleAvoidanceVector = neighborsInCollisionArea.foldLeft(Point3D.Zero)(_ + _) * -1
    println(obstacleAvoidanceVector)
    val resultingVector = (Point3D(local._1, local._2, 0) * 0.5) + obstacleAvoidanceVector
    val res =
      if distanceTowardGoal < stabilityThreshold then Rotation(0, 1)
      else Forward((resultingVector.x / distanceTowardGoal, resultingVector.y / distanceTowardGoal))
    res

  protected def orderedNodes(nodes: Set[(Int, (Double, Double))]): List[(Int, (Double, Double))] =
    nodes.filter(_._1 != mid()).toList.sortBy(_._1)

class LineFormation(distanceThreshold: Double, leaderSelected: Int, stabilityThreshold: Double, collisionArea: Double)
    extends ShapeFormation(leaderSelected, stabilityThreshold, collisionArea: Double):
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

class CircleFormation(radius: Double, leaderSelected: Int, stabilityThreshold: Double, collisionArea: Double)
    extends ShapeFormation(leaderSelected, stabilityThreshold, collisionArea: Double):
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
