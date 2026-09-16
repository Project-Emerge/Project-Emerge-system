package it.unibo.demo.scenarios

/**
 * Solves the minimum weight perfect matching problem (the assignment problem) for
 * multi-robot shape formations.
 *
 * Matching is done on squared distances, which is what keeps robots from crossing paths
 * and keeps total travel low.
 *
 * The implementation is the Hungarian algorithm (Jonker-Volgenant shortest augmenting
 * paths with potentials), which is exact and runs in O(n^3). It replaces an earlier
 * branch-and-bound search: that was also exact, but its bound collapsed precisely in the
 * case the runtime hits most -- a scattered fleet still far from its slots -- where it cost
 * ~11ms for 9 robots, ~90ms for 10 and over a second for 11. At 12 robots that alone
 * stretched the 200ms control loop past a second and destabilised the wheel controllers.
 */
object AssignmentSolver:

  type RobotId = Int
  type Vector2D = (Double, Double)

  /** Stands in for a distance that is not a real number, so one bad pose cannot poison the solve. */
  private val UnusableCost: Double = 1e12

  /**
   * Solves the assignment problem, matching each robot to a target slot optimally.
   *
   * @param robots  A list of robots, each containing its unique ID and its current position vector relative to the leader.
   * @param targets A list of target slot positions relative to the leader.
   * @return A map associating each robot ID with the relative vector pointing from the robot to its assigned target slot.
   */
  def solve(
    robots: List[(RobotId, Vector2D)],
    targets: List[Vector2D]
  ): Map[RobotId, Vector2D] =
    displacements(robots, targets, solveIndices(robots, targets))

  /**
   * The displacement each robot must travel under the given assignment.
   *
   * @param assignment robot id to the index of the slot it was given
   */
  def displacements(
    robots: List[(RobotId, Vector2D)],
    targets: List[Vector2D],
    assignment: Map[RobotId, Int]
  ): Map[RobotId, Vector2D] =
    val targetArray = targets.toArray
    robots.iterator.flatMap { (id, at) =>
      assignment.get(id).filter(targetArray.indices.contains).map { slot =>
        val (tx, ty) = targetArray(slot)
        id -> (tx + at._1, ty + at._2)
      }
    }.toMap

  /** Total squared distance travelled under the given assignment. */
  def cost(
    robots: List[(RobotId, Vector2D)],
    targets: List[Vector2D],
    assignment: Map[RobotId, Int]
  ): Double =
    displacements(robots, targets, assignment).values.foldLeft(0.0) { (total, d) =>
      total + d._1 * d._1 + d._2 * d._2
    }

  /**
   * The optimal assignment, as a map from robot id to the index of the slot it was given.
   *
   * Note that when several assignments share the lowest cost -- which is common for a
   * collinear slot set -- which one is returned is arbitrary. Callers that feed this to
   * moving robots should hold the previous assignment steady rather than adopt a new
   * equal-cost one every round; see `ShapeFormation.steadyAssignment`.
   */
  def solveIndices(
    robots: List[(RobotId, Vector2D)],
    targets: List[Vector2D]
  ): Map[RobotId, Int] =
    if robots.isEmpty || targets.isEmpty || robots.size != targets.size then
      Map.empty
    else
      val n = robots.size
      val robotArray = robots.toArray
      val targetArray = targets.toArray

      // cost(r)(t) is the squared distance robot r would travel to reach target t.
      // Robot vectors point from the robot to the anchor, so the robot's position relative
      // to the anchor is -r and the displacement to target t is t - (-r) = t + r.
      val cost = Array.tabulate(n, n) { (r, t) =>
        val (rx, ry) = robotArray(r)._2
        val (tx, ty) = targetArray(t)
        val dx = rx + tx
        val dy = ry + ty
        val squared = dx * dx + dy * dy
        if squared.isNaN || squared.isInfinite then UnusableCost else squared
      }

      // Jonker-Volgenant, 1-indexed with a sentinel column 0 as in the classic formulation.
      // rowPotential/columnPotential keep the reduced costs non-negative; matchedRow(j) is
      // the robot currently assigned to slot j; parent(j) reconstructs the augmenting path.
      val rowPotential = Array.fill(n + 1)(0.0)
      val columnPotential = Array.fill(n + 1)(0.0)
      val matchedRow = Array.fill(n + 1)(0)
      val parent = Array.fill(n + 1)(0)

      for robot <- 1 to n do
        matchedRow(0) = robot
        var current = 0
        val slack = Array.fill(n + 1)(Double.PositiveInfinity)
        val visited = Array.fill(n + 1)(false)

        // Grow a shortest augmenting path until it reaches a slot with no robot on it.
        var reachedFreeSlot = false
        while !reachedFreeSlot do
          visited(current) = true
          val onPath = matchedRow(current)
          var delta = Double.PositiveInfinity
          var next = 0

          var slot = 1
          while slot <= n do
            if !visited(slot) then
              val reduced = cost(onPath - 1)(slot - 1) - rowPotential(onPath) - columnPotential(slot)
              if reduced < slack(slot) then
                slack(slot) = reduced
                parent(slot) = current
              if slack(slot) < delta then
                delta = slack(slot)
                next = slot
            slot += 1

          // Shift the potentials so the chosen slot becomes tight, keeping the rest valid.
          var j = 0
          while j <= n do
            if visited(j) then
              rowPotential(matchedRow(j)) += delta
              columnPotential(j) -= delta
            else slack(j) -= delta
            j += 1

          current = next
          reachedFreeSlot = matchedRow(current) == 0

        // Walk the path back, moving each robot onto the next slot along it.
        while current != 0 do
          val previous = parent(current)
          matchedRow(current) = matchedRow(previous)
          current = previous

      val resultMap = Map.newBuilder[RobotId, Int]
      for slot <- 1 to n do
        resultMap += (robotArray(matchedRow(slot) - 1)._1 -> (slot - 1))

      resultMap.result()
