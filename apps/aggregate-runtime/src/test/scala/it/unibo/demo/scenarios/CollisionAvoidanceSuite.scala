package it.unibo.demo.scenarios

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.AllDemoToLoad
import it.unibo.demo.robot.*
import it.unibo.utils.Position.given

/** Real aggregate rounds with prescribed slots isolate steering from assignment changes. */
class CollisionAvoidanceSuite extends munit.FunSuite:
  private type Position = (Double, Double)

  private class SteeringProbe extends ShapeFormation():
    override protected def slots(ctx: SlotContext): List[Position] = Nil
    override def logic(): Actuation = actuate(
      AnchorFrame(sense[Boolean]("testRoot"), 0.0, (0.0, 0.0), 0.0),
      sense[Position]("testGoal")
    )

  private final class World(
      val positions: Map[Int, Position],
      goals: Map[Int, Position],
      settings: Map[String, Any] = Map.empty
  ) extends Environment[Int, Position, Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): Position = positions(id)
    override def neighbors(id: Int): Set[Int] = nodes
    override def sensing(id: Int): Map[String, Any] =
      val at = positions(id)
      val target = goals.getOrElse(id, at)
      FormationDefaults.All ++ settings ++ Map(
        BaseDemo.Orientation -> 0.0,
        "testGoal" -> (target._1 - at._1, target._2 - at._2),
        "testRoot" -> !goals.contains(id)
      )

  private def orchestrator = AggregateOrchestrator[Position, Actuation](SteeringProbe())
  private val start = Map(0 -> (0.0, 0.0), 1 -> (0.0, 0.28))
  private val goal = Map(1 -> (0.0, -0.4))

  private def tick(loop: AggregateOrchestrator[Position, Actuation], world: World): Map[Int, Actuation] =
    val result = loop.tick(world)
    assertEquals(result.keySet, world.nodes, "every device must complete its aggregate round")
    result

  private def forward(actuation: Actuation): (Position, Double) = actuation match
    case Actuation.Forward(direction, distance) =>
      assert(direction._1.isFinite && direction._2.isFinite && distance.isFinite)
      assertEqualsDouble(math.hypot(direction._1, direction._2), 1.0, 1e-9)
      (direction, distance)
    case other => fail(s"expected a travel command, got $other")

  private def warmed(): AggregateOrchestrator[Position, Actuation] =
    val loop = orchestrator
    (1 to 3).foreach(_ => tick(loop, World(start, goal)))
    loop

  test("collinear attraction and repulsion produce a detour without inflating goal distance") {
    val commands = tick(warmed(), World(start, goal))
    val (direction, distance) = forward(commands(1))
    assert(direction._1 > 0.5, s"a collinear obstacle needs a lateral escape: $direction")
    assertEqualsDouble(distance, 0.68, 1e-9)
    assertEquals(commands(0), Actuation.NoOp)
  }

  test("5 mm pose noise and repeated influence-boundary crossings do not change sides") {
    val loop = warmed()
    for round <- 1 to 80 do
      val x = if round % 2 == 0 then 0.005 else -0.005
      val y = if round % 3 == 0 then 0.31 else 0.29
      val (direction, _) = forward(tick(loop, World(start.updated(1, (x, y)), goal))(1))
      assert(direction._1 > 0.0, s"round $round switched sides: $direction")
  }

  test("the detour starts continuously at the anticipation radius") {
    val loop = warmed()
    val headings = List(0.346, 0.345, 0.344).map { y =>
      val (direction, _) = forward(tick(loop, World(start.updated(1, (0.0, y)), goal))(1))
      math.atan2(direction._2, direction._1)
    }
    headings.sliding(2).foreach { pair =>
      assert(math.abs(DifferentialDrive.normalizeAngle(pair(1) - pair(0))) < 0.05)
    }
  }

  test("a brief missing observation remembers the side but produces no stale force") {
    val loop = warmed()
    (1 to 2).foreach { _ =>
      val (direction, _) = forward(tick(loop, World(start - 0, goal))(1))
      assertEqualsDouble(direction._1, 0.0, 1e-9)
      assertEqualsDouble(direction._2, -1.0, 1e-9)
    }
    // This geometry would favour the other side if the choice had been forgotten.
    val (direction, _) = forward(tick(loop, World(start.updated(1, (-0.005, 0.28)), goal))(1))
    assert(direction._1 > 0.0)
  }

  test("a departed obstacle releases the choice and a new encounter can choose the other side") {
    val loop = warmed()
    (1 to 4).foreach(_ => tick(loop, World(start - 0, goal)))
    val world = World(start.updated(1, (-0.005, 0.28)), goal)
    (1 to 2).foreach(_ => tick(loop, world))
    val (direction, _) = forward(tick(loop, world)(1))
    assert(direction._1 < 0.0)
  }

  test("a clear path releases the detour and does not keep circling the obstacle") {
    val loop = warmed()
    val clearGoal = Map(1 -> (0.0, 0.9))
    (1 to 4).foreach(_ => tick(loop, World(start, clearGoal)))
    val (direction, _) = forward(tick(loop, World(start, clearGoal))(1))
    assertEqualsDouble(direction._1, 0.0, 1e-9)
    assertEqualsDouble(direction._2, 1.0, 1e-9)
    val (returned, _) = forward(tick(loop, World(start.updated(1, (-0.005, 0.28)), goal))(1))
    assert(returned._1 < 0.0, "a later encounter must choose its own side")
  }

  test("arrival and becoming the anchor both clear the previous choice") {
    List(goal.updated(1, start(1)), Map.empty[Int, Position]).foreach { destinations =>
      val loop = warmed()
      tick(loop, World(start, destinations))
      val (direction, _) = forward(tick(loop, World(start.updated(1, (-0.005, 0.28)), goal))(1))
      assert(direction._1 < 0.0, "the old side survived arrival or a change of anchor")
    }
  }

  test("other neighbours still repel a robot while it keeps its chosen detour") {
    val loop = warmed()
    val crowded = start + (2 -> (0.15, 0.28))
    (1 to 2).foreach(_ => tick(loop, World(crowded, goal)))
    val (direction, _) = forward(tick(loop, World(crowded, goal))(1))
    assert(direction._1 < 0.0, s"the close neighbour on the right must still repel: $direction")
    val (resumed, _) = forward(tick(loop, World(start, goal))(1))
    assert(resumed._1 > 0.0, "removing the extra neighbour must resume the same detour")
  }

  test("a distant former blocker cannot prevent avoiding a closer neighbour") {
    val loop = warmed()
    val changed = Map(0 -> (0.0, -0.7), 1 -> (0.0, 0.28), 2 -> (0.0, 0.0))
    val distantGoal = Map(1 -> (0.0, -1.2))
    (1 to 5).foreach(_ => tick(loop, World(changed, distantGoal)))
    val (direction, _) = forward(tick(loop, World(changed, distantGoal))(1))
    assert(direction._1 > 0.0, "the nearby obstacle must get a tangent after the old one leaves")
  }

  test("a frontal pair chooses opposite physical sides and reaches both goals") {
    val loop = orchestrator
    var positions = Map(1 -> (-0.6, 0.0), 2 -> (0.6, 0.0))
    val goals = Map(1 -> (0.6, 0.0), 2 -> (-0.6, 0.0))
    var separation = Double.PositiveInfinity
    var deviated = Set.empty[Int]
    (1 to 400).foreach { _ =>
      val commands = tick(loop, World(positions, goals))
      positions = positions.map { (id, at) =>
        commands(id) match
          case Actuation.Forward(direction, distance) =>
            val step = math.min(0.01, distance)
            id -> (at._1 + direction._1 * step, at._2 + direction._2 * step)
          case _ => id -> at
      }
      val a = positions(1)
      val b = positions(2)
      separation = math.min(separation, math.hypot(a._1 - b._1, a._2 - b._2))
      if a._2 > 0.02 then deviated += 1
      if b._2 < -0.02 then deviated += 2
    }
    assertEquals(deviated, Set(1, 2))
    assert(separation > 0.2, s"the pair got too close: $separation m")
    goals.foreach { (id, target) =>
      val at = positions(id)
      assert(math.hypot(at._1 - target._1, at._2 - target._2) < 0.1, s"$id stopped at $at")
    }
  }

  test("switching formation during a detour starts fresh and keeps every round aligned") {
    val loop = AggregateOrchestrator[Position, Actuation](AllDemoToLoad(
      "verticalLineShape" -> VerticalLineFormation(), "lineShape" -> LineFormation()
    ))
    val settings = Map[String, Any](BaseDemo.Leader -> 0, BaseDemo.Program -> "verticalLineShape")
    (1 to 20).foreach(_ => tick(loop, World(start, goal, settings)))
    val (before, _) = forward(tick(loop, World(start, goal, settings))(1))
    assert(before._1 > 0.0)
    (1 to 10).foreach(_ => tick(loop, World(start, goal, settings.updated(BaseDemo.Program, "lineShape"))))
    val moved = start.updated(1, (-0.005, 0.28))
    (1 to 20).foreach(_ => tick(loop, World(moved, goal, settings)))
    val (after, _) = forward(tick(loop, World(moved, goal, settings))(1))
    assert(after._1 < 0.0, "returning to a formation must not resurrect its old detour")
  }

  test("zero influence and coincident observations produce finite commands") {
    List(0.0, 0.3).foreach { radius =>
      val loop = orchestrator
      val coincident = Map(0 -> (0.0, 0.28), 1 -> (0.0, 0.28))
      val world = World(coincident, goal, Map(BaseDemo.CollisionArea -> radius))
      (1 to 3).foreach(_ => tick(loop, world))
      val (direction, distance) = forward(tick(loop, world)(1))
      assertEqualsDouble(direction._2, -1.0, 1e-9)
      assertEqualsDouble(distance, 0.68, 1e-9)
    }
  }
