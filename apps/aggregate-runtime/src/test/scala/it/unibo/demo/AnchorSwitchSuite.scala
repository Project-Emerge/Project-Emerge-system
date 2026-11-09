package it.unibo.demo

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.*
import it.unibo.utils.Position.given

/**
 * Switching between a named leader and an elected one changes which field computations a
 * device runs -- `isRootDevice` branches on it -- so it changes the shape of that device's
 * export. Neighbours that disagree about the choice, which happens whenever the operator
 * switches because `MqttProvider` stamps each robot's config as its own pose arrives, must
 * not read each other's slots as the wrong type.
 */
class AnchorSwitchSuite extends munit.FunSuite:

  /** Lets each robot report its own config, to reproduce a half-applied change. */
  private final class SplitWorld(
      val positions: Map[Int, (Double, Double)],
      val configFor: Int => Map[String, Any]
  ) extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = configFor(id) + (BaseDemo.Orientation -> 0.2)
    override def neighbors(id: Int): Set[Int] = positions.keySet

  private val base: Map[String, Any] = FormationDefaults.All + (BaseDemo.Program -> "circleShape")

  private val twelve: Map[Int, (Double, Double)] =
    (0 until 12).map(i => i -> ((i % 4) * 0.6 + 0.2, (i / 4) * 0.6 + 0.2)).toMap

  private def namedConfig = base ++ Map(BaseDemo.Anchor -> BaseDemo.AnchorLeader, BaseDemo.Leader -> 3)
  private def electedConfig =
    base ++ Map(BaseDemo.Anchor -> BaseDemo.AnchorAuto, BaseDemo.Leader -> BaseDemo.NoLeader)

  /**
   * Ticks and insists every robot got a command. The orchestrator under test is built with
   * the default `haltOnFailure = None`, so a robot whose round threw is simply absent from
   * the result -- which makes this a real assertion that nothing threw, rather than a check
   * that the failure was merely caught.
   */
  private def tickAll(
      orchestrator: AggregateOrchestrator[(Double, Double), Actuation],
      world: SplitWorld,
      rounds: Int,
      phase: String
  ): Unit =
    (1 to rounds).foreach { round =>
      val actuations = orchestrator.tick(world)
      assertEquals(
        actuations.size,
        world.positions.size,
        s"$phase round $round: ${world.positions.size - actuations.size} robot(s) failed their round"
      )
    }

  test("naming a leader and then clearing it does not corrupt the round") {
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](CircleFormation())
    tickAll(orchestrator, SplitWorld(twelve, _ => namedConfig), 20, "named leader")
    // Now every robot flips to an election, reusing the exports from before the flip.
    tickAll(orchestrator, SplitWorld(twelve, _ => electedConfig), 20, "leader cleared")
  }

  test("a half-applied change does not corrupt the round") {
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](CircleFormation())
    tickAll(orchestrator, SplitWorld(twelve, _ => namedConfig), 20, "before the switch")
    // The retained command reaches half the fleet a tick before the other half.
    val halfApplied = SplitWorld(twelve, id => if id % 2 == 0 then electedConfig else namedConfig)
    tickAll(orchestrator, halfApplied, 10, "half applied")
  }

  test("switching back to a named leader also survives") {
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](CircleFormation())
    tickAll(orchestrator, SplitWorld(twelve, _ => electedConfig), 15, "elected first")
    tickAll(orchestrator, SplitWorld(twelve, _ => namedConfig), 15, "back to a named leader")
    tickAll(orchestrator, SplitWorld(twelve, _ => electedConfig), 15, "elected again")
  }

  test("naming a leader that is not in the fleet does not corrupt the round") {
    // The absent leader is detected as a field computation, so this path evaluates the same
    // constructs as a present one and must stay aligned with it.
    val absent = base ++ Map(BaseDemo.Anchor -> BaseDemo.AnchorLeader, BaseDemo.Leader -> 99)
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](CircleFormation())
    tickAll(orchestrator, SplitWorld(twelve, _ => namedConfig), 15, "named leader")
    tickAll(orchestrator, SplitWorld(twelve, _ => absent), 15, "absent leader")
    tickAll(orchestrator, SplitWorld(twelve, _ => namedConfig), 15, "named again")
  }

  test("a robot whose round fails recovers instead of staying stuck") {
    // Half-applied changes make individual rounds fail; the fleet must be fully healthy
    // again once every robot agrees, which only happens if a failed robot's stale export
    // is dropped rather than retained.
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](CircleFormation())
    (1 to 20).foreach(_ => orchestrator.tick(SplitWorld(twelve, _ => namedConfig)))
    // Deliberately inconsistent for a while, so some rounds really do fail.
    (1 to 10).foreach(_ =>
      orchestrator.tick(SplitWorld(twelve, id => if id % 3 == 0 then electedConfig else namedConfig))
    )
    // Then everybody agrees; within a few rounds nothing may still be failing.
    (1 to 10).foreach(_ => orchestrator.tick(SplitWorld(twelve, _ => electedConfig)))
    tickAll(orchestrator, SplitWorld(twelve, _ => electedConfig), 10, "after settling")
  }
