package it.unibo.demo

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.*
import it.unibo.utils.Position.given

/**
 * A fleet that has reached its shape must stop moving.
 *
 * The thing that used to prevent that is the assignment: several matchings can share the
 * lowest cost, especially for a symmetric or collinear shape, and adopting a new equal-cost
 * one every round makes robots swap slots and drive past each other, which moves them,
 * which flips the optimum again. So `SlotAssignment.steadyAssignment` holds the matching in
 * hand unless a fresh one is better by a margin.
 *
 * The vertical line also covers avoidance: a robot on the wrong side of the anchor must
 * go around it. Pure radial repulsion used to leave one robot bouncing back and forth
 * indefinitely instead of reaching its slot.
 */
class AssignmentChurnSuite extends munit.FunSuite:

  private final class World(val positions: Map[Int, (Double, Double)], val config: Map[String, Any])
      extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = config + (BaseDemo.Orientation -> 0.0)
    override def neighbors(id: Int): Set[Int] = positions.keySet

  private val base: Map[String, Any] =
    BaseDemo.Defaults ++ LineFormation.DEFAULTS ++ VerticalLineFormation.DEFAULTS
      ++ CircleFormation.DEFAULTS ++ SquareFormation.DEFAULTS ++ HeartFormation.DEFAULTS
      ++ VFormation.DEFAULTS ++ ShapeFormation.DEFAULTS
      ++ Map(
        BaseDemo.Anchor -> BaseDemo.AnchorLeader,
        BaseDemo.Leader -> 0,
        BaseDemo.CollisionArea -> 0.3,
        BaseDemo.StabilityThreshold -> 0.1
      )

  /**
   * Total distance every robot travels over the final rounds. A settled fleet travels none.
   * The dead band mirrors the real controller, which ignores commands under 3cm.
   */
  private def residualMotion(
      program: BaseDemo,
      config: Map[String, Any],
      robots: Int,
      rounds: Int = 700,
      tail: Int = 200,
      step: Double = 0.03
  ): (Double, Map[Int, (Double, Double)]) =
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](program)
    var positions = (0 until robots).map(i => i -> ((i % 4) * 0.7 + 0.3, (i / 4) * 0.7 + 0.3)).toMap
    var travelled = 0.0
    (1 to rounds).foreach { round =>
      val actuations = orchestrator.tick(World(positions, config))
      positions = positions.map { (id, at) =>
        actuations.get(id) match
          case Some(Actuation.Forward(direction, distance)) if distance >= 0.03 =>
            val moved = math.min(distance, step)
            if round > rounds - tail then travelled += moved
            id -> (at._1 + direction._1 * moved, at._2 + direction._2 * moved)
          case _ => id -> at
      }
    }
    (travelled, positions)

  test("every static shape settles completely once it has converged") {
    List(
      "circleShape" -> (() => CircleFormation()),
      "squareShape" -> (() => SquareFormation()),
      "heartShape" -> (() => HeartFormation()),
      "vShape" -> (() => VFormation()),
      "verticalLineShape" -> (() => VerticalLineFormation()),
      // A line is the shape equal-cost matchings hurt most, since every slot of it is
      // interchangeable with its mirror image.
      "lineShape" -> (() => LineFormation())
    ).foreach { (name, build) =>
      val (moved, settled) = residualMotion(build(), base + (BaseDemo.Program -> name), 12)
      println(f"RESIDUAL $name%-18s = $moved%7.3f m")
      assertEquals(moved, 0.0, s"$name kept moving after settling: $moved m")
      if name == "verticalLineShape" then
        val anchor = settled(0)
        val spacing = VerticalLineFormation.DEFAULTS(VerticalLineFormation.INTER_DISTANCE_SENSING)
        val claimed = (settled - 0).values.map { at =>
          val slot = (1 until settled.size).minBy(i => math.abs(at._2 - (anchor._2 - i * spacing)))
          val error = math.hypot(at._1 - anchor._1, at._2 - (anchor._2 - slot * spacing))
          assert(error < 0.1, s"a vertical-line robot stopped $error m from its slot at $at")
          slot
        }.toList
        assertEquals(claimed.distinct.size, settled.size - 1, "every vertical-line slot must be occupied")
    }
  }

  test("a line settles whatever the size of the fleet") {
    // The residual used to grow with the fleet, so this sweeps rather than picking one size.
    (9 to 13).foreach { robots =>
      val (moved, _) = residualMotion(LineFormation(), base + (BaseDemo.Program -> "lineShape"), robots)
      println(f"RESIDUAL lineShape n=$robots%-2d       = $moved%7.3f m")
      assertEquals(moved, 0.0, s"a line of $robots robots kept moving: $moved m")
    }
  }
