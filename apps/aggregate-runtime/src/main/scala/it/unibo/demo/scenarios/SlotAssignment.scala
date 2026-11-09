package it.unibo.demo.scenarios

import it.unibo.demo.scenarios.AssignmentSolver.{RobotId, Vector2D}

/**
 * Slot bookkeeping for [[ShapeFormation]]. Only the root evaluates these, inside `plan`'s
 * `branch`, so the `rep` state lives on exactly one device.
 */
trait SlotAssignment extends BaseDemo:
  import SlotAssignment.*

  /**
   * Everyone the collect reached, plus whoever it reached recently enough to count as briefly
   * out of sight. A robot the tracker blinks out for one round would otherwise change the slot
   * count, re-lay the whole shape, and change it back when the robot reappears.
   */
  protected def steadyParticipants(reached: Map[RobotId, Vector2D]): List[(RobotId, Vector2D)] =
    rep(Map.empty[RobotId, (Vector2D, Int)])(remember(reached)).view
      .mapValues(_._1)
      .toList
      .sortBy(_._1)

  private def remember(reached: Map[RobotId, Vector2D])(
      previous: Map[RobotId, (Vector2D, Int)]
  ): Map[RobotId, (Vector2D, Int)] =
    val stillMissed = previous.collect {
      case (id, (offset, rounds)) if !reached.contains(id) && rounds + 1 < ParticipantMemoryRounds =>
        id -> (offset, rounds + 1)
    }
    stillMissed ++ reached.view.mapValues(offset => (offset, 0)).toMap

  /**
   * The optimal matching, adopted only when it beats the one in hand by a margin. Collinear
   * slots admit many matchings of the same cost, and swapping between them makes robots drive
   * past each other, which flips the optimum again: the fleet never settles.
   */
  protected def steadyAssignment(
      robots: List[(RobotId, Vector2D)],
      targets: List[Vector2D]
  ): Map[RobotId, Vector2D] =
    val held = rep(Map.empty[RobotId, Int]) { previous =>
      val fresh = AssignmentSolver.solveIndices(robots, targets)
      if !isUsable(previous, robots, targets) || isCheaper(fresh, previous, robots, targets) then fresh
      else previous
    }
    AssignmentSolver.displacements(robots, targets, held)

  /** An assignment survives only while it still covers exactly these robots and these slots. */
  private def isUsable(
      assignment: Map[RobotId, Int],
      robots: List[(RobotId, Vector2D)],
      targets: List[Vector2D]
  ): Boolean =
    assignment.nonEmpty
      && assignment.keySet == robots.iterator.map(_._1).toSet
      && assignment.values.forall(targets.indices.contains)
      && assignment.values.toSet.size == targets.size

  private def isCheaper(
      fresh: Map[RobotId, Int],
      held: Map[RobotId, Int],
      robots: List[(RobotId, Vector2D)],
      targets: List[Vector2D]
  ): Boolean =
    AssignmentSolver.cost(robots, targets, fresh) <
      AssignmentSolver.cost(robots, targets, held) - AssignmentSwitchMargin

object SlotAssignment:
  /**
   * Rounds an unreached robot keeps its slot: long enough to ride out tracker gaps, short
   * enough that a robot which has genuinely left is not held a slot for long.
   */
  val ParticipantMemoryRounds: Int = 10

  /** How much better, in total squared metres, a fresh matching must be to be taken up. */
  val AssignmentSwitchMargin: Double = 0.05
