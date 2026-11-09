package it.unibo.demo

import it.unibo.demo.scenarios.*

/**
 * Guards the two properties the formation pipeline needs from the assignment solver:
 * the matching must be optimal, and it must stay cheap for a whole fleet.
 *
 * The scale test is the regression guard for a real outage: the previous
 * branch-and-bound solver was also optimal, but on a scattered fleet still far from its
 * slots it took ~1.1s for 11 robots, which stretched the 200ms control loop past a second
 * and left the wheel controllers oscillating instead of forming a shape.
 */
class AssignmentSolverScaleSuite extends munit.FunSuite:

  private def costOf(robot: (Int, (Double, Double)), target: (Double, Double)): Double =
    val dx = robot._2._1 + target._1
    val dy = robot._2._2 + target._2
    dx * dx + dy * dy

  test("the matching is optimal, checked against brute force") {
    val random = new scala.util.Random(1234)
    (2 to 7).foreach { n =>
      (1 to 20).foreach { _ =>
        val robots = (1 to n).map(i => i -> (random.between(-3.0, 3.0), random.between(-3.0, 3.0))).toList
        val targets = (1 to n).map(_ => (random.between(-3.0, 3.0), random.between(-3.0, 3.0))).toList
        val brute = targets.permutations.map(order => robots.zip(order).map(costOf).sum).min
        val solved = AssignmentSolver.solve(robots, targets)
        val actual = robots.map { robot =>
          val displacement = solved(robot._1)
          displacement._1 * displacement._1 + displacement._2 * displacement._2
        }.sum
        assertEqualsDouble(actual, brute, 1e-9, s"suboptimal matching for n=$n")
      }
    }
  }

  test("a scattered fleet far from its slots is solved quickly at fleet scale") {
    // The shape the runtime actually feeds it: robots spread over the arena, vectors
    // pointing back to the anchor, targets bunched onto a small ring.
    def scattered(n: Int): (List[(Int, (Double, Double))], List[(Double, Double)]) =
      val grid = (0 until n + 1).map(i => ((i % 5) * 2.5 + 0.5, (i / 5) * 2.0 + 0.5)).toList
      val anchor = grid.head
      val robots = grid.zipWithIndex.tail.map { (at, i) => i -> (anchor._1 - at._1, anchor._2 - at._2) }
      (robots, ShapeFormation.ring(n, 0.0)(_ => 0.6))

    (8 to 40).foreach { n =>
      val (robots, targets) = scattered(n)
      assertEquals(AssignmentSolver.solve(robots, targets).size, n)
    }

    // Deliberately generous, so this fails on an exponential regression but not on a slow
    // machine: the old solver needed over a second for 11 robots alone.
    val (robots, targets) = scattered(40)
    val started = System.nanoTime()
    (1 to 20).foreach(_ => AssignmentSolver.solve(robots, targets))
    val perSolve = (System.nanoTime() - started) / 1e6 / 20
    assert(perSolve < 100.0, f"a 40-robot assignment took $perSolve%.1f ms, expected well under 100 ms")
  }
