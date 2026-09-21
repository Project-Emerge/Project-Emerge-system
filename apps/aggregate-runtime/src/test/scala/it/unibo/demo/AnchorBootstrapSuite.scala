package it.unibo.demo

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.*
import it.unibo.utils.Position.given

/**
 * What the fleet does in the rounds *before* it has settled, which
 * [[AssignmentChurnSuite]] deliberately does not look at.
 *
 * The anchor is decided by two field computations that both need a few rounds to cross the
 * fleet: the distance to a named leader starts at +Infinity everywhere, and sparse choice
 * opens with every device claiming to be a leader and gives the claim up one hop per round.
 * Acting on either before it has settled puts several anchors in the fleet at once, each
 * planning a shape out of the handful of offsets its own collect has reached and
 * broadcasting it -- and the robots physically drive those plans for the several further
 * rounds it takes them to wash out of the collect/broadcast pipeline. The visible symptom
 * is a fleet that lurches about before it starts converging.
 */
class AnchorBootstrapSuite extends munit.FunSuite:

  private final class World(val positions: Map[Int, (Double, Double)], val config: Map[String, Any])
      extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = config + (BaseDemo.Orientation -> 0.0)
    override def neighbors(id: Int): Set[Int] = positions.keySet

  private val base: Map[String, Any] =
    FormationDefaults.All ++ Map(BaseDemo.CollisionArea -> 0.3, BaseDemo.StabilityThreshold -> 0.1)

  private val named: Map[String, Any] =
    Map(BaseDemo.Anchor -> BaseDemo.AnchorLeader, BaseDemo.Leader -> 0)

  private val elected: Map[String, Any] =
    Map(BaseDemo.Anchor -> BaseDemo.AnchorAuto, BaseDemo.Leader -> BaseDemo.NoLeader)

  private val twelve: Map[Int, (Double, Double)] =
    (0 until 12).map(i => i -> ((i % 4) * 0.7 + 0.3, (i / 4) * 0.7 + 0.3)).toMap

  /** `Stop` gives the anchor `NoOp` and everyone else `Stop`, which makes the anchor visible. */
  private def anchorsPerRound(config: Map[String, Any], rounds: Int): List[Set[Int]] =
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](Stop())
    (1 to rounds).map { _ =>
      orchestrator.tick(World(twelve, config)).collect { case (id, Actuation.NoOp) => id }.toSet
    }.toList

  test("no round ever has the fleet believing in more than one anchor") {
    List("a named leader" -> named, "an election" -> elected).foreach { (label, anchorConfig) =>
      anchorsPerRound(base ++ anchorConfig, 40).zipWithIndex.foreach { (anchors, index) =>
        assert(
          anchors.size <= 1,
          s"with $label, round ${index + 1} had ${anchors.size} anchors: $anchors"
        )
      }
    }
  }

  test("a named leader anchors the fleet from the very first round") {
    // The distance field that detects an absent leader has not crossed the fleet yet at this
    // point, and waiting for it is what used to hand round one to the election.
    assertEquals(anchorsPerRound(base ++ named, 1), List(Set(0)))
  }

  /**
   * Every robot's travel over the whole run, transient included. A fleet that reverses out
   * of a plan it should never have been given pays for it here, in metres.
   */
  private def totalTravel(
      program: BaseDemo,
      config: Map[String, Any],
      rounds: Int = 400,
      step: Double = 0.03,
      warmupRounds: Int = 0
  ): Double =
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](program)
    var positions = twelve
    var travelled = 0.0
    (1 to warmupRounds).foreach(_ => orchestrator.tick(World(positions, config)))
    (1 to rounds).foreach { _ =>
      val actuations = orchestrator.tick(World(positions, config))
      positions = positions.map { (id, at) =>
        actuations.get(id) match
          case Some(Actuation.Forward(direction, distance)) if distance >= 0.03 =>
            val moved = math.min(distance, step)
            travelled += moved
            id -> (at._1 + direction._1 * moved, at._2 + direction._2 * moved)
          case _ => id -> at
      }
    }
    travelled

  test("bootstrapping adds little travel compared with a settled planning field") {
    // Compare the same steering policy from the same positions with and without a settled
    // field. Committed detours legitimately cost more than radial steering, so old absolute
    // route budgets no longer isolate bootstrap waste. The former startup burst added 9%
    // for a square and 13% for a line; a 5% overhead limit still catches those regressions.
    List(
      ("circleShape", () => CircleFormation()),
      ("lineShape", () => LineFormation()),
      ("squareShape", () => SquareFormation())
    ).foreach { (name, build) =>
      List("a named leader" -> named, "an election" -> elected).foreach { (label, anchorConfig) =>
        val config = base ++ anchorConfig + (BaseDemo.Program -> name)
        val travelled = totalTravel(build(), config)
        val planned = totalTravel(build(), config, warmupRounds = 40)
        assert(
          travelled <= planned * 1.05,
          f"$name with $label travelled $travelled%.2f m; the settled field needs $planned%.2f m"
        )
      }
    }
  }
