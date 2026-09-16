package it.unibo.demo.scenarios

/**
 * `AssignmentSolver` is the one piece every shape depends on, and its sign convention is
 * easy to get wrong: robot vectors point *from the robot to the anchor*, and the result is
 * the displacement *from the robot to its slot*.
 */
class AssignmentSolverSuite extends munit.FunSuite:

  test("a robot standing on its slot is told to stay put") {
    // The robot is 1m south of the anchor, so its vector to the anchor is (0, 1);
    // its slot is also 1m south of the anchor, at (0, -1).
    val solved = AssignmentSolver.solve(List(1 -> (0.0, 1.0)), List((0.0, -1.0)))
    assertEqualsDouble(solved(1)._1, 0.0, 1e-9)
    assertEqualsDouble(solved(1)._2, 0.0, 1e-9)
  }

  test("the displacement points from the robot to the slot it was given") {
    // Robot at the anchor, slot 0.5m east of it.
    val solved = AssignmentSolver.solve(List(1 -> (0.0, 0.0)), List((0.5, 0.0)))
    assertEqualsDouble(solved(1)._1, 0.5, 1e-9)
    assertEqualsDouble(solved(1)._2, 0.0, 1e-9)
  }

  test("robots take the nearest slots rather than crossing paths") {
    // Two robots either side of the anchor, and a slot on each side.
    val west = 1 -> (1.0, 0.0)  // robot 1 is 1m west, so the anchor is 1m east of it
    val east = 2 -> (-1.0, 0.0) // robot 2 is 1m east
    val solved = AssignmentSolver.solve(List(west, east), List((-1.0, 0.0), (1.0, 0.0)))
    // Each should barely move: crossing over would cost far more.
    assertEqualsDouble(solved(1)._1, 0.0, 1e-9)
    assertEqualsDouble(solved(2)._1, 0.0, 1e-9)
  }

  test("a mismatched number of robots and slots yields no plan at all") {
    assertEquals(AssignmentSolver.solve(List(1 -> (0.0, 0.0)), List((1.0, 0.0), (2.0, 0.0))), Map.empty)
    assertEquals(AssignmentSolver.solve(Nil, List((1.0, 0.0))), Map.empty)
    assertEquals(AssignmentSolver.solve(List(1 -> (0.0, 0.0)), Nil), Map.empty)
  }

  test("every robot receives exactly one slot") {
    val robots = (1 to 6).map(id => id -> (id * 0.1, id * -0.2)).toList
    val slots = ShapeFormation.ring(6, 0.0)(_ => 0.6)
    val solved = AssignmentSolver.solve(robots, slots)
    assertEquals(solved.keySet, robots.map(_._1).toSet)
  }
