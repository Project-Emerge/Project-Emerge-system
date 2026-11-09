package it.unibo.demo

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.*
import it.unibo.utils.Position.given

/**
 * Drives real aggregate rounds against a synthetic world, with no broker and no robots.
 *
 * This is where the leader election is actually exercised: the slot geometry can be checked
 * as pure maths, but whether the fleet elects exactly one leader, and whether it recovers
 * when that leader leaves, only shows up once rounds are run.
 */
class AggregateFormationSuite extends munit.FunSuite:

  /** A fully connected fleet, which is what `neighborhood-system` serves by default. */
  private final class FakeWorld(
      val positions: Map[Int, (Double, Double)],
      val config: Map[String, Any]
  ) extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = config + (BaseDemo.Orientation -> 0.0)
    // Self-inclusive, mirroring what MqttProvider builds from /neighbors/<id>.
    override def neighbors(id: Int): Set[Int] = positions.keySet

  // The shipped defaults, so this suite cannot fall behind a formation that gained a parameter:
  // a molecule nobody seeded throws inside the round and halts that robot.
  private val baseConfig: Map[String, Any] = FormationDefaults.All + (BaseDemo.Program -> "stop")

  private def orchestratorFor(program: BaseDemo) =
    AggregateOrchestrator[(Double, Double), Actuation](program)

  /** Runs `rounds` rounds over a fixed world and returns the last round's actuations. */
  private def settle(
      program: BaseDemo,
      positions: Map[Int, (Double, Double)],
      config: Map[String, Any],
      rounds: Int = 30
  ): Map[Int, Actuation] =
    val orchestrator = orchestratorFor(program)
    var last = Map.empty[Int, Actuation]
    (1 to rounds).foreach(_ => last = orchestrator.tick(FakeWorld(positions, config)))
    last

  /** `Stop` gives the root `NoOp` and everybody else `Stop`, which makes the root visible. */
  private def rootsOf(actuations: Map[Int, Actuation]): Set[Int] =
    actuations.collect { case (id, Actuation.NoOp) => id }.toSet

  private val fourRobots = Map(
    1 -> (0.0, 0.0),
    2 -> (0.5, 0.0),
    3 -> (0.0, 0.5),
    4 -> (0.5, 0.5)
  )

  test("with no leader chosen, the fleet elects exactly one of its own") {
    val roots = rootsOf(settle(Stop(), fourRobots, baseConfig))
    assertEquals(roots.size, 1, s"expected a single elected leader, got $roots")
  }

  test("a lone robot elects itself rather than waiting for a leader that cannot exist") {
    // The regression that matters: a distance field seeded with `center` instead of +Inf
    // reads 0 here, so the robot believes it is standing on an absent leader and no
    // election ever completes.
    val roots = rootsOf(settle(Stop(), Map(7 -> (0.0, 0.0)), baseConfig))
    assertEquals(roots, Set(7))
  }

  test("the election survives the elected leader leaving the fleet") {
    val orchestrator = orchestratorFor(Stop())
    (1 to 30).foreach(_ => orchestrator.tick(FakeWorld(fourRobots, baseConfig)))
    val firstRoot = rootsOf(orchestrator.tick(FakeWorld(fourRobots, baseConfig))).head

    val survivors = fourRobots - firstRoot
    var latest = Map.empty[Int, Actuation]
    (1 to 30).foreach(_ => latest = orchestrator.tick(FakeWorld(survivors, baseConfig)))

    val newRoots = rootsOf(latest)
    assertEquals(newRoots.size, 1, s"expected exactly one leader after the loss, got $newRoots")
    assert(!newRoots.contains(firstRoot), "the departed leader was re-elected")
  }

  test("an explicitly chosen leader is the one used") {
    val config = baseConfig ++ Map(BaseDemo.Leader -> 3, BaseDemo.Anchor -> BaseDemo.AnchorLeader)
    assertEquals(rootsOf(settle(Stop(), fourRobots, config)), Set(3))
  }

  test("a chosen leader that is not in the fleet falls back to an election") {
    // Previously this silently rooted every gradient on a device id nobody had, and the
    // formation no-opped without saying anything.
    val config = baseConfig ++ Map(BaseDemo.Leader -> 99, BaseDemo.Anchor -> BaseDemo.AnchorLeader)
    val roots = rootsOf(settle(Stop(), fourRobots, config))
    assertEquals(roots.size, 1, s"expected a fallback election, got $roots")
    assert(fourRobots.keySet.contains(roots.head))
  }

  test("a leader-anchored circle leaves the leader standing at the centre") {
    val config = baseConfig ++ Map(
      BaseDemo.Program -> "circleShape",
      BaseDemo.Leader -> 1,
      BaseDemo.Anchor -> BaseDemo.AnchorLeader
    )
    val actuations = settle(CircleFormation(), fourRobots, config)
    assertEquals(actuations.size, 4)
    // The leader occupies the origin of its own formation, so it is never given a slot.
    assertEquals(actuations(1), Actuation.NoOp)
    (2 to 4).foreach(id => assertNotEquals(actuations(id), Actuation.NoOp))
  }

  test("an elected leader stands at the centre just as a chosen one does") {
    val config = baseConfig ++ Map(
      BaseDemo.Program -> "circleShape",
      BaseDemo.Anchor -> BaseDemo.AnchorAuto,
      BaseDemo.Leader -> BaseDemo.NoLeader
    )
    val actuations = settle(CircleFormation(), fourRobots, config)
    assertEquals(actuations.size, 4)
    assertEquals(rootsOf(actuations).size, 1, "exactly one robot must hold the centre")
  }

  test("no formation ever emits a NaN heading or distance") {
    val programs: List[(String, BaseDemo)] = List(
      "circleShape" -> CircleFormation(),
      "squareShape" -> SquareFormation(),
      "heartShape" -> HeartFormation(),
      "ringWave" -> RingWaveFormation(),
      "breathingCircle" -> BreathingCircleFormation(),
      "orbitCircle" -> OrbitFormation(),
      "sineLine" -> SineLineFormation()
    )
    val anchors = List(BaseDemo.AnchorLeader, BaseDemo.AnchorAuto)
    for
      (name, program) <- programs
      anchor <- anchors
    do
      val config = baseConfig ++ Map(
        BaseDemo.Program -> name,
        BaseDemo.Anchor -> anchor,
        BaseDemo.Leader -> (if anchor == BaseDemo.AnchorLeader then 1 else BaseDemo.NoLeader)
      )
      settle(program, fourRobots, config, rounds = 15).foreach { (id, actuation) =>
        actuation match
          case Actuation.Forward(direction, distance) =>
            assert(
              !direction._1.isNaN && !direction._2.isNaN && !distance.isNaN,
              s"$name/$anchor produced a NaN Forward for robot $id"
            )
          case Actuation.Rotation(vector) =>
            assert(
              !vector._1.isNaN && !vector._2.isNaN,
              s"$name/$anchor produced a NaN Rotation for robot $id"
            )
          case _ => ()
      }
  }

  /**
   * Integrates the actuations into motion, so a whole formation can be checked for the
   * thing that actually matters: does the fleet end up in the shape it was asked for.
   * Only translation is modelled -- `Rotation` and `Stop` leave a robot where it is.
   */
  private def converge(
      program: BaseDemo,
      start: Map[Int, (Double, Double)],
      config: Map[String, Any],
      rounds: Int = 400,
      step: Double = 0.02
  ): Map[Int, (Double, Double)] =
    val orchestrator = orchestratorFor(program)
    var positions = start
    (1 to rounds).foreach { _ =>
      val actuations = orchestrator.tick(FakeWorld(positions, config))
      positions = positions.map { (id, at) =>
        actuations.get(id) match
          case Some(Actuation.Forward(direction, distance)) =>
            val travelled = math.min(distance, step)
            id -> (at._1 + direction._1 * travelled, at._2 + direction._2 * travelled)
          case _ => id -> at
      }
    }
    positions

  private def distance(a: (Double, Double), b: (Double, Double)): Double =
    math.hypot(a._1 - b._1, a._2 - b._2)

  test("a leader-anchored circle converges into a ring around the chosen leader") {
    val radius = 0.7
    val config = baseConfig ++ Map(
      BaseDemo.Program -> "circleShape",
      BaseDemo.Leader -> 1,
      BaseDemo.Anchor -> BaseDemo.AnchorLeader,
      CircleFormation.RADIUS_SENSING -> radius
    )
    val settled = converge(CircleFormation(), fourRobots, config)
    val centre = settled(1)
    (2 to 4).foreach { id =>
      val actual = distance(settled(id), centre)
      assert(
        math.abs(actual - radius) < 0.12,
        s"robot $id settled ${actual}m from the leader, expected about ${radius}m"
      )
    }
  }

  test("a circle around an elected leader converges just as well") {
    val radius = 0.6
    val config = baseConfig ++ Map(
      BaseDemo.Program -> "circleShape",
      BaseDemo.Anchor -> BaseDemo.AnchorAuto,
      BaseDemo.Leader -> BaseDemo.NoLeader,
      CircleFormation.RADIUS_SENSING -> radius
    )
    val settled = converge(CircleFormation(), fourRobots, config)
    // Which robot won the election is not fixed by the test, so ask for it: `isRootDevice`
    // is shared by every program, and `Stop` reports it as the only `NoOp`.
    val leader = rootsOf(settle(Stop(), settled, config + (BaseDemo.Program -> "stop"))).head
    (fourRobots.keySet - leader).foreach { id =>
      val actual = distance(settled(id), settled(leader))
      assert(
        math.abs(actual - radius) < 0.15,
        s"robot $id settled ${actual}m from the elected leader, expected about ${radius}m"
      )
    }
  }

  /**
   * The time-varying shapes are driven by the shared clock, which the orchestrator samples
   * from the wall clock once per round. Rounds here run far faster than real time, so the
   * phase barely advances; sampling the slot layout directly at chosen instants is what
   * actually shows the motion.
   */
  test("an orbiting ring keeps its radius but advances its bearing over the cycle") {
    val radius = 0.6
    val quarterCycle = ShapeFormation.phaseAt(1_000L, 4.0) // a quarter of a 4s cycle
    val atRest = ShapeFormation.ring(4, 0.0)(_ => radius)
    val turned = ShapeFormation.ring(4, quarterCycle)(_ => radius)

    atRest.zip(turned).foreach { (before, after) =>
      assertEqualsDouble(math.hypot(before._1, before._2), radius, 1e-9)
      assertEqualsDouble(math.hypot(after._1, after._2), radius, 1e-9)
      assert(
        math.hypot(after._1 - before._1, after._2 - before._2) > 1e-3,
        "the ring did not turn at all"
      )
    }
  }

  test("a travelling wave moves its crest around the ring as the cycle advances") {
    def radii(phase: Double): List[Double] =
      ShapeFormation
        .ring(8, 0.0) { i =>
          val spatial = 2 * math.Pi * 1.0 * i / 8
          math.max(ShapeFormation.MinRadius, 0.6 + 0.2 * math.sin(phase - spatial))
        }
        .map(slot => math.hypot(slot._1, slot._2))

    val start = radii(0.0)
    val later = radii(math.Pi / 2)
    // The crest is the widest slot; it must have moved to a different robot.
    val crestAtStart = start.indexOf(start.max)
    val crestLater = later.indexOf(later.max)
    assertNotEquals(crestAtStart, crestLater, s"the crest stayed on slot $crestAtStart")
  }
