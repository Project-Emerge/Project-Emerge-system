package it.unibo.demo.scenarios

/**
 * An idiomatic and highly optimized Scala 3 solver for the minimum weight perfect matching problem
 * (the assignment problem) specifically tailored for multi-robot shape formations.
 *
 * It uses a Branch and Bound backtracking algorithm to find the globally optimal bijection
 * between robots and target slots, minimizing the sum of squared distances to completely
 * prevent path crossings and minimize total travel time.
 */
object AssignmentSolver:

  type RobotId = Int
  type Vector2D = (Double, Double)

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
    if robots.isEmpty || targets.isEmpty || robots.size != targets.size then
      Map.empty
    else
      val n = robots.size
      val robotArray = robots.toArray
      val targetArray = targets.toArray

      // Precompute squared distance matrix: distSq(r)(t) is the squared distance from robot r to target t
      val distSq = Array.tabulate(n, n) { (r, t) =>
        val (rx, ry) = robotArray(r)._2
        val (tx, ty) = targetArray(t)
        val dx = rx + tx
        val dy = ry + ty
        dx * dx + dy * dy
      }

      var bestCost = Double.MaxValue
      val bestMatch = new Array[Int](n) // Maps target index -> robot index

      // 1. Greedy Initialization (provides a strong initial upper bound for pruning)
      val assignedRobots = new Array[Boolean](n)
      var greedyCost = 0.0
      val tempMatch = new Array[Int](n)
      for t <- 0 until n do
        val bestR = (0 until n)
          .filter(r => !assignedRobots(r))
          .minByOption(r => distSq(r)(t))
        
        bestR.foreach { r =>
          assignedRobots(r) = true
          tempMatch(t) = r
          greedyCost += distSq(r)(t)
        }

      bestCost = greedyCost
      tempMatch.copyToArray(bestMatch)

      // 2. Precompute optimistic suffix bounds for aggressive pruning
      val minTargetCost = Array.tabulate(n) { t =>
        (0 until n).map(r => distSq(r)(t)).min
      }

      val suffixMinCost = new Array[Double](n + 1)
      for tIndex <- (n - 1) to 0 by -1 do
        suffixMinCost(tIndex) = suffixMinCost(tIndex + 1) + minTargetCost(tIndex)

      // 3. Pre-sort candidate robots for each target to visit better assignments first
      val sortedRobotsForTarget = Array.tabulate(n) { tg =>
        (0 until n).sortBy(r => distSq(r)(tg)).toArray
      }

      // 4. Backtracking search with branch-and-bound pruning
      val currentMatch = new Array[Int](n)
      val usedRobots = new Array[Boolean](n)

      def search(targetIdx: Int, currentCost: Double): Unit =
        if currentCost + suffixMinCost(targetIdx) >= bestCost then
          () // Prune branch
        else if targetIdx == n then
          if currentCost < bestCost then
            bestCost = currentCost
            currentMatch.copyToArray(bestMatch)
        else
          val candidates = sortedRobotsForTarget(targetIdx)
          for r <- candidates do
            if !usedRobots(r) then
              val cost = distSq(r)(targetIdx)
              if currentCost + cost + suffixMinCost(targetIdx + 1) < bestCost then
                usedRobots(r) = true
                currentMatch(targetIdx) = r
                search(targetIdx + 1, currentCost + cost)
                usedRobots(r) = false

      search(0, 0.0)

      // 5. Construct the result mapping (RobotId -> Relative Displacement Vector)
      val resultMap = Map.newBuilder[RobotId, Vector2D]
      for tg <- 0 until n do
        val robotIdx = bestMatch(tg)
        val (robotId, (rx, ry)) = robotArray(robotIdx)
        val (tx, ty) = targetArray(tg)
        val relativeVector = (tx + rx, ty + ry)
        resultMap += (robotId -> relativeVector)

      resultMap.result()
