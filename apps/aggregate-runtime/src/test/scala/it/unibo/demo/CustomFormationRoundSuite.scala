package it.unibo.demo

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.provider.CustomSpecCodec
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.*
import it.unibo.utils.Position.given

/**
 * Drives real aggregate rounds for the data-driven formation, which is where the claims the
 * geometry suite cannot check are settled: that a spec need not participate in alignment, that
 * the fleet follows the spec its root holds, and that a missing spec degrades into a visible
 * shape rather than a fleet-wide pivot.
 */
class CustomFormationRoundSuite extends munit.FunSuite:

  private final class FakeWorld(
      val positions: Map[Int, (Double, Double)],
      val configFor: Int => Map[String, Any]
  ) extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = configFor(id) + (BaseDemo.Orientation -> 0.0)
    override def neighbors(id: Int): Set[Int] = positions.keySet

  private def spec(json: String): CustomSpec = CustomSpecCodec.decode(ujson.read(json))

  private val base: Map[String, Any] = FormationDefaults.All ++ Map(
    BaseDemo.Program -> "custom",
    BaseDemo.Anchor -> BaseDemo.AnchorLeader,
    BaseDemo.Leader -> 1
  )

  private def withSpec(value: CustomSpec, extra: Map[String, Any] = Map.empty): Map[String, Any] =
    base ++ extra + (CustomFormation.SPEC_SENSING -> value)

  private val fiveRobots = Map(
    1 -> (0.0, 0.0),
    2 -> (0.4, 0.1),
    3 -> (0.1, 0.4),
    4 -> (0.5, 0.5),
    5 -> (-0.3, 0.2)
  )

  /**
   * `haltOnFailure = None` on purpose: a robot whose round threw is then simply absent from the
   * result, which is what lets these tests assert that nothing threw.
   */
  private def orchestratorFor(program: BaseDemo) =
    AggregateOrchestrator[(Double, Double), Actuation](program)

  private def settle(
      program: BaseDemo,
      positions: Map[Int, (Double, Double)],
      configFor: Int => Map[String, Any],
      rounds: Int = 30
  ): Map[Int, Actuation] =
    val orchestrator = orchestratorFor(program)
    var last = Map.empty[Int, Actuation]
    (1 to rounds).foreach(_ => last = orchestrator.tick(FakeWorld(positions, configFor)))
    last

  private def converge(
      program: BaseDemo,
      start: Map[Int, (Double, Double)],
      configFor: Int => Map[String, Any],
      rounds: Int = 400,
      step: Double = 0.02
  ): Map[Int, (Double, Double)] =
    val orchestrator = orchestratorFor(program)
    var positions = start
    (1 to rounds).foreach { _ =>
      val actuations = orchestrator.tick(FakeWorld(positions, configFor))
      positions = positions.map { (id, at) =>
        actuations.get(id) match
          case Some(Actuation.Forward(direction, distance)) =>
            val travelled = math.min(distance, step)
            id -> (at._1 + direction._1 * travelled, at._2 + direction._2 * travelled)
          case _ => id -> at
      }
    }
    positions

  test("a custom points formation converges into the requested shape") {
    val geometry = spec("""{"kind":"points","points":[[0,0.6],[0.6,-0.3],[-0.6,-0.3]],"closed":true}""")
    val config = withSpec(geometry)
    val settled = converge(CustomFormation(), fiveRobots, _ => config)
    val leader = settled(1)
    val expected = CustomSlots
      .slotsFor(geometry, SlotContext(4, 0.0, 0.0), 1.0, 1.5, 0.3)
      .getOrElse(fail("the spec produced no slots"))
    val followers = (settled - 1).values.toList
    // Each follower reaches some slot, and no slot takes two of them.
    val claimed = followers.map { at =>
      val relative = (at._1 - leader._1, at._2 - leader._2)
      val (slot, index) = expected.zipWithIndex
        .minBy((slot, _) => math.hypot(slot._1 - relative._1, slot._2 - relative._2))
      assert(
        math.hypot(slot._1 - relative._1, slot._2 - relative._2) < 0.2,
        s"$relative reached no slot of $expected"
      )
      index
    }
    assertEquals(claimed.distinct.size, claimed.size, s"two robots shared a slot: $claimed")
  }

  test("a custom formation with no spec falls back to a ring rather than freezing") {
    // An empty slot list would not be a hold: `actuate` gives the root `NoOp` and every other
    // robot `Rotation`, so the whole fleet pivots on the spot and reads as a malfunction.
    val actuations = settle(CustomFormation(), fiveRobots, _ => withSpec(CustomSpec.Absent))
    assertEquals(actuations.size, fiveRobots.size)
    val followers = actuations - 1
    followers.foreach { (id, actuation) =>
      assert(actuation.isInstanceOf[Actuation.Forward], s"robot $id got $actuation, not Forward")
    }
    val settled = converge(CustomFormation(), fiveRobots, _ => withSpec(CustomSpec.Absent))
    val leader = settled(1)
    val radius = FormationDefaults.All(CircleFormation.RADIUS_SENSING).asInstanceOf[Double]
    (settled - 1).foreach { (id, at) =>
      val distance = math.hypot(at._1 - leader._1, at._2 - leader._2)
      assertEqualsDouble(distance, radius, 0.15, s"robot $id settled at $distance, not $radius")
    }
  }

  test("a rejected spec behaves exactly like an absent one") {
    val absent = settle(CustomFormation(), fiveRobots, _ => withSpec(CustomSpec.Absent))
    val rejected = settle(CustomFormation(), fiveRobots, _ => withSpec(CustomSpec.Invalid("nope")))
    assertEquals(rejected.keySet, absent.keySet)
    rejected.foreach { (id, actuation) =>
      assertEquals(actuation.getClass, absent(id).getClass, s"robot $id diverged")
    }
  }

  test("a custom formation never emits a NaN heading or distance") {
    val hostile = List(
      spec("""{"kind":"cartesian","x":"1/i","y":"log(0-1)"}"""),
      spec("""{"kind":"cartesian","x":"exp(exp(i))","y":"0^0"}"""),
      spec("""{"kind":"polar","r":"0-5","theta":"tan(pi/2)"}"""),
      spec("""{"kind":"points","points":[[0,0],[0,0],[0,0]]}"""),
      spec("""{"kind":"points","points":[[3,-3],[0,0]],"closed":true}"""),
      CustomSpec.Invalid("rejected"),
      CustomSpec.Absent
    )
    for
      geometry <- hostile
      anchor <- List(BaseDemo.AnchorLeader, BaseDemo.AnchorAuto)
    do
      val config = withSpec(
        geometry,
        Map(
          BaseDemo.Anchor -> anchor,
          BaseDemo.Leader -> (if anchor == BaseDemo.AnchorLeader then 1 else BaseDemo.NoLeader)
        )
      )
      val actuations = settle(CustomFormation(), fiveRobots, _ => config, rounds = 15)
      assertEquals(actuations.size, fiveRobots.size, s"a round threw for $geometry / $anchor")
      actuations.foreach { (id, actuation) =>
        actuation match
          case Actuation.Forward(direction, distance) =>
            assert(direction._1.isFinite && direction._2.isFinite, s"$id: $direction")
            assert(distance.isFinite, s"$id: $distance")
          case Actuation.Rotation(x, y) =>
            assert(x.isFinite && y.isFinite, s"$id: rotation ($x, $y)")
          case _ => ()
      }
  }

  test("a half-applied spec change does not corrupt the round") {
    // The alignment claim, made concrete. MqttProvider stamps each robot's config as its own pose
    // arrives, so a disagreement is guaranteed on every publish; if the spec reached an `align`
    // key this would split the fleet into two mutually invisible sub-networks.
    val old = spec("""{"kind":"polar","r":"0.5","theta":"2*pi*i/n"}""")
    val fresh = spec("""{"kind":"points","points":[[0,0.7],[0.7,0],[0,-0.7],[-0.7,0]],"closed":true}""")
    val orchestrator = orchestratorFor(CustomFormation())
    val split = (id: Int) => withSpec(if id % 2 == 0 then fresh else old)
    (1 to 10).foreach { round =>
      val actuations = orchestrator.tick(FakeWorld(fiveRobots, split))
      assertEquals(actuations.size, fiveRobots.size, s"a robot lost its round at split round $round")
    }
    (1 to 10).foreach { round =>
      val actuations = orchestrator.tick(FakeWorld(fiveRobots, _ => withSpec(fresh)))
      assertEquals(actuations.size, fiveRobots.size, s"a robot lost its round at settled round $round")
    }
  }

  test("the fleet executes the spec its root holds") {
    // Only the root evaluates `slots`, and it broadcasts the plan as data -- which is exactly why
    // the spec need not participate in alignment.
    val ring = spec("""{"kind":"polar","r":"0.5","theta":"2*pi*i/n"}""")
    val line = spec("""{"kind":"cartesian","x":"0","y":"0.35*(i+1)"}""")
    val everyone = settle(CustomFormation(), fiveRobots, _ => withSpec(line), rounds = 20)
    val rootOnly = settle(
      CustomFormation(),
      fiveRobots,
      id => withSpec(if id == 1 then line else ring),
      rounds = 20
    )
    // The root holds `line` in both, so the commands must agree.
    rootOnly.foreach { (id, actuation) =>
      assertEquals(actuation, everyone(id), s"robot $id followed a non-root spec")
    }
    // And with the root alone on the old spec, the fleet keeps building the old shape.
    val rootStale = settle(
      CustomFormation(),
      fiveRobots,
      id => withSpec(if id == 1 then ring else line),
      rounds = 20
    )
    val allRing = settle(CustomFormation(), fiveRobots, _ => withSpec(ring), rounds = 20)
    rootStale.foreach { (id, actuation) =>
      assertEquals(actuation, allRing(id), s"robot $id ignored the root's spec")
    }
    // Without this the test could pass by the two specs simply commanding the same thing.
    assertNotEquals(allRing, everyone, "the ring and the line command the same actuations")
  }

  test("switching into and out of custom does not corrupt the round") {
    // `align(currentProgram)` keys on the program name, so `custom` is one more scope. A
    // half-applied switch is the transient AnchorSwitchSuite already covers for the anchor.
    val geometry = spec("""{"kind":"polar","r":"0.5","theta":"2*pi*i/n + t"}""")
    // Each simulation needs its own Scafi VM; the registry's instances are shared with other
    // suites, which sbt runs concurrently.
    val orchestrator = orchestratorFor(AllDemoToLoad("circleShape" -> CircleFormation(), "custom" -> CustomFormation()))
    def config(program: String) = withSpec(geometry) + (BaseDemo.Program -> program)
    List("circleShape", "custom", "circleShape", "custom").foreach { program =>
      (1 to 15).foreach { round =>
        val actuations = orchestrator.tick(FakeWorld(fiveRobots, _ => config(program)))
        assertEquals(actuations.size, fiveRobots.size, s"a robot lost its round on $program/$round")
      }
      // One deliberately half-applied tick between the two programs.
      val mixed = orchestrator.tick(
        FakeWorld(fiveRobots, id => config(if id % 2 == 0 then program else "custom"))
      )
      assertEquals(mixed.size, fiveRobots.size, s"a robot lost its round switching off $program")
    }
  }

  test("a spec that puts every slot on the anchor does not stack the fleet") {
    val onAnchor = spec("""{"kind":"points","points":[[0,0]]}""")
    val settled = converge(CustomFormation(), fiveRobots, _ => withSpec(onAnchor))
    val gap = ShapeFormation.clearance(
      FormationDefaults.All(BaseDemo.CollisionArea).asInstanceOf[Double],
      FormationDefaults.All(BaseDemo.StabilityThreshold).asInstanceOf[Double]
    )
    val pairs = settled.values.toList.combinations(2).toList
    pairs.foreach { case List(a, b) =>
      assert(
        math.hypot(a._1 - b._1, a._2 - b._2) > gap / 2,
        s"$a and $b ended up on top of each other"
      )
    }
  }

  test("a rotating custom formation keeps its radii as the phase advances") {
    // Rounds run far faster than real time, so sample the layout directly at two phases, exactly
    // as the existing orbit test does.
    val geometry = spec("""{"kind":"polar","r":"0.6","theta":"2*pi*i/n + t"}""")
    val at = (phase: Double) => CustomSlots
      .slotsFor(geometry, SlotContext(6, phase, 0.0), 1.0, 1.5, 0.3)
      .getOrElse(fail("the spec produced no slots"))
    val still = at(0.0)
    val turned = at(math.Pi / 3)
    still.zip(turned).foreach { case (before, after) =>
      assertEqualsDouble(math.hypot(after._1, after._2), math.hypot(before._1, before._2), 1e-9)
      assert(math.hypot(after._1 - before._1, after._2 - before._2) > 1e-3, "the ring did not turn")
    }
  }
