package it.unibo.demo.scenarios

/**
 * Minimum weight perfect matching for shape formations, on squared distances - which is what
 * keeps robots from crossing paths.
 */
object AssignmentSolver:

  type RobotId = Int
  type Vector2D = (Double, Double)

  /** Stands in for a non-finite distance, so one bad pose cannot poison the solve. */
  private val UnusableCost: Double = 1e12

  /** Robot id -> the vector from the robot to its assigned slot. Positions are leader-relative. */
  def solve(
    robots: List[(RobotId, Vector2D)],
    targets: List[Vector2D]
  ): Map[RobotId, Vector2D] =
    displacements(robots, targets, solveIndices(robots, targets))

  /** The displacement each robot must travel, given a robot id -> slot index assignment. */
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
   * Robot id -> slot index. Ties are common (collinear slots) and broken arbitrarily, so callers
   * driving real robots must hold the previous assignment steady: `SlotAssignment.steadyAssignment`.
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

      // Robot vectors point from the robot to the anchor, so the displacement to t is t - (-r).
      val cost = Array.tabulate(n, n) { (r, t) =>
        val (rx, ry) = robotArray(r)._2
        val (tx, ty) = targetArray(t)
        val dx = rx + tx
        val dy = ry + ty
        val squared = dx * dx + dy * dy
        if squared.isNaN || squared.isInfinite then UnusableCost else squared
      }

      // Jonker-Volgenant, 1-indexed with a sentinel column 0. The potentials keep reduced costs
      // non-negative; matchedRow(j) is the robot on slot j; parent(j) rebuilds the augmenting path.
      val rowPotential = Array.fill(n + 1)(0.0)
      val columnPotential = Array.fill(n + 1)(0.0)
      val matchedRow = Array.fill(n + 1)(0)
      val parent = Array.fill(n + 1)(0)

      for robot <- 1 to n do
        matchedRow(0) = robot
        var current = 0
        val slack = Array.fill(n + 1)(Double.PositiveInfinity)
        val visited = Array.fill(n + 1)(false)

        // Grow a shortest augmenting path until it reaches a free slot.
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

          // Shift potentials so the chosen slot becomes tight, keeping the rest valid.
          var j = 0
          while j <= n do
            if visited(j) then
              rowPotential(matchedRow(j)) += delta
              columnPotential(j) -= delta
            else slack(j) -= delta
            j += 1

          current = next
          reachedFreeSlot = matchedRow(current) == 0

        // Walk back, moving each robot onto the next slot along the path.
        while current != 0 do
          val previous = parent(current)
          matchedRow(current) = matchedRow(previous)
          current = previous

      val resultMap = Map.newBuilder[RobotId, Int]
      for slot <- 1 to n do
        resultMap += (robotArray(matchedRow(slot) - 1)._1 -> (slot - 1))

      resultMap.result()
