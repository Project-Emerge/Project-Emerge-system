package it.unibo.demo

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.formation.{Fuel, SharedCompiler}
import it.unibo.demo.provider.{CustomKind, CustomRequest, CustomSpecCodec}
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.*
import it.unibo.utils.Position.given

/**
 * Drives real aggregate rounds for designed formations, which is where the claims the geometry
 * suite cannot check are settled: that a designed geometry need not participate in alignment,
 * that the fleet follows the geometry its root holds, that a missing or failing one degrades into
 * a visible shape rather than a fleet-wide pivot, and that a designed program takes the fleet over
 * exactly like a built-in one.
 */
class CustomFormationRoundSuite extends munit.FunSuite:

  override val munitTimeout = scala.concurrent.duration.Duration(180, "s")

  private final class FakeWorld(
      val positions: Map[Int, (Double, Double)],
      val configFor: Int => Map[String, Any]
  ) extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = configFor(id) + (BaseDemo.Orientation -> 0.0)
    override def neighbors(id: Int): Set[Int] = positions.keySet

  private def compiled(kind: CustomKind, source: String): CustomSpec =
    CustomSpecCodec.compile(SharedCompiler.instance, CustomRequest.Compile(kind, source)) match
      case CustomSpec.Invalid(reason) => fail(s"expected `$source` to compile: $reason")
      case spec => spec

  private def geometry(source: String): CustomSpec = compiled(CustomKind.Formation, source)

  private def program(source: String): CustomSpec = compiled(CustomKind.Program, source)

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

  private val triangle = List((0.0, 0.6), (0.6, -0.3), (-0.6, -0.3))
  private val triangleSource = "Formation(ctx => outline(ctx.count, closed = true)((0.0, 0.6), (0.6, -0.3), (-0.6, -0.3)))"
  private val ringSource = "Formation(ctx => ring(ctx.count, 0.5))"
  private val lineSource = "Formation(ctx => (0 until ctx.count).toList.map(i => (0.0, 0.35 * (i + 1))))"
  private val spinningSource = "Formation(ctx => ring(ctx.count, 0.5).rotated(ctx.phase))"

  /**
   * `haltOnFailure = None` on purpose: a robot whose round threw is then simply absent from the
   * result, which is what lets these tests assert that nothing threw.
   */
  private def orchestratorFor(program: BaseDemo, halt: Option[Actuation] = None) =
    AggregateOrchestrator[(Double, Double), Actuation](program, halt)

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

  test("a designed outline converges into the requested shape") {
    val outline = geometry(triangleSource)
    val settled = converge(CustomFormation(), fiveRobots, _ => withSpec(outline))
    val leader = settled(1)
    val expected = CustomSlots
      .slotsFor(CustomSlots.resample(triangle, closed = true, count = 4), 4, 1.0, 1.5, 0.3)
      .getOrElse(fail("the outline produced no slots"))
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

  test("a custom formation with no geometry falls back to a ring rather than freezing") {
    // An empty slot list would not be a hold: `actuate` gives the root `NoOp` and every other
    // robot `Rotation`, so the whole fleet pivots on the spot and reads as a malfunction.
    val actuations = settle(CustomFormation(), fiveRobots, _ => withSpec(CustomSpec.Absent))
    assertEquals(actuations.size, fiveRobots.size)
    (actuations - 1).foreach { (id, actuation) =>
      assert(actuation.isInstanceOf[Actuation.Forward], s"robot $id got $actuation, not Forward")
    }
    val settled = converge(CustomFormation(), fiveRobots, _ => withSpec(CustomSpec.Absent))
    val leader = settled(1)
    val radius = FormationDefaults.All(CircleFormation.RADIUS_SENSING) match
      case value: Double => value
      case other => fail(s"the default radius is not a number: $other")
    (settled - 1).foreach { (id, at) =>
      val distance = math.hypot(at._1 - leader._1, at._2 - leader._2)
      assertEqualsDouble(distance, radius, 0.15, s"robot $id settled at $distance, not $radius")
    }
  }

  /** The fallback, round for round, so the failures below can be compared against it. */
  private def assertBehavesLikeTheFallback(spec: CustomSpec): Unit =
    val absent = settle(CustomFormation(), fiveRobots, _ => withSpec(CustomSpec.Absent))
    val failing = settle(CustomFormation(), fiveRobots, _ => withSpec(spec))
    assertEquals(failing.keySet, absent.keySet)
    failing.foreach { (id, actuation) =>
      assertEquals(actuation.getClass, absent(id).getClass, s"robot $id diverged under $spec")
    }

  test("a rejected geometry behaves exactly like an absent one") {
    assertBehavesLikeTheFallback(CustomSpec.Invalid("nope"))
  }

  test("a geometry that runs out of fuel falls back to the ring instead of stalling the round") {
    val endless = geometry("{ def spin(k: Long): Long = spin(k + 1); Formation(ctx => ring(ctx.count, spin(0).toDouble)) }")
    assertBehavesLikeTheFallback(endless)
  }

  test("a geometry that throws, or lays out the wrong number of slots, falls back to the ring") {
    assertBehavesLikeTheFallback(geometry("Formation(ctx => List.empty[Point].head :: ring(ctx.count, 0.5))"))
    assertBehavesLikeTheFallback(geometry("Formation(_ => List((0.5, 0.5)))"))
  }

  test("a custom formation never emits a NaN heading or distance") {
    val hostile = List(
      geometry("Formation(ctx => (0 until ctx.count).toList.map(i => (1.0 / i, math.log(-1.0))))"),
      geometry("Formation(ctx => (0 until ctx.count).toList.map(i => (math.exp(math.exp(i)), math.pow(0, 0))))"),
      geometry("Formation(ctx => ring(ctx.count, -5).rotated(math.tan(math.Pi / 2)))"),
      geometry("Formation(ctx => outline(ctx.count, closed = false)((0.0, 0.0), (0.0, 0.0), (0.0, 0.0)))"),
      geometry("Formation(ctx => outline(ctx.count, closed = true)((3.0, -3.0), (0.0, 0.0)))"),
      CustomSpec.Invalid("rejected"),
      CustomSpec.Absent
    )
    for
      spec <- hostile
      anchor <- List(BaseDemo.AnchorLeader, BaseDemo.AnchorAuto)
    do
      val config = withSpec(
        spec,
        Map(
          BaseDemo.Anchor -> anchor,
          BaseDemo.Leader -> (if anchor == BaseDemo.AnchorLeader then 1 else BaseDemo.NoLeader)
        )
      )
      val actuations = settle(CustomFormation(), fiveRobots, _ => config, rounds = 15)
      assertEquals(actuations.size, fiveRobots.size, s"a round threw for $spec / $anchor")
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

  test("a half-applied geometry change does not corrupt the round") {
    // The alignment claim, made concrete. MqttProvider stamps each robot's config as its own pose
    // arrives, so a disagreement is guaranteed on every publish; if the geometry reached an `align`
    // key this would split the fleet into two mutually invisible sub-networks.
    val old = geometry(ringSource)
    val fresh = geometry("Formation(ctx => outline(ctx.count, closed = true)((0.0, 0.7), (0.7, 0.0), (0.0, -0.7), (-0.7, 0.0)))")
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

  test("the fleet executes the geometry its root holds") {
    // Only the root evaluates `slots`, and it broadcasts the plan as data -- which is exactly why
    // the geometry need not participate in alignment.
    val ring = geometry(ringSource)
    val line = geometry(lineSource)
    val everyone = settle(CustomFormation(), fiveRobots, _ => withSpec(line), rounds = 20)
    val rootOnly = settle(CustomFormation(), fiveRobots, id => withSpec(if id == 1 then line else ring), rounds = 20)
    rootOnly.foreach { (id, actuation) =>
      assertEquals(actuation, everyone(id), s"robot $id followed a non-root geometry")
    }
    val rootStale = settle(CustomFormation(), fiveRobots, id => withSpec(if id == 1 then ring else line), rounds = 20)
    val allRing = settle(CustomFormation(), fiveRobots, _ => withSpec(ring), rounds = 20)
    rootStale.foreach { (id, actuation) =>
      assertEquals(actuation, allRing(id), s"robot $id ignored the root's geometry")
    }
    // Without this the test could pass by the two geometries simply commanding the same thing.
    assertNotEquals(allRing, everyone, "the ring and the line command the same actuations")
  }

  test("switching into and out of custom does not corrupt the round") {
    // `align(currentProgram)` keys on the program name, so `custom` is one more scope. A
    // half-applied switch is the transient AnchorSwitchSuite already covers for the anchor.
    val spinning = geometry(spinningSource)
    // Each simulation needs its own Scafi VM; the registry's instances are shared with other
    // suites, which sbt runs concurrently.
    val orchestrator = orchestratorFor(AllDemoToLoad("circleShape" -> CircleFormation(), "custom" -> CustomFormation()))
    def config(program: String) = withSpec(spinning) + (BaseDemo.Program -> program)
    List("circleShape", "custom", "circleShape", "custom").foreach { program =>
      (1 to 15).foreach { round =>
        val actuations = orchestrator.tick(FakeWorld(fiveRobots, _ => config(program)))
        assertEquals(actuations.size, fiveRobots.size, s"a robot lost its round on $program/$round")
      }
      // One deliberately half-applied tick between the two programs.
      val mixed = orchestrator.tick(FakeWorld(fiveRobots, id => config(if id % 2 == 0 then program else "custom")))
      assertEquals(mixed.size, fiveRobots.size, s"a robot lost its round switching off $program")
    }
  }

  test("a geometry that puts every slot on the anchor does not stack the fleet") {
    val onAnchor = geometry("Formation(ctx => outline(ctx.count, closed = false)((0.0, 0.0)))")
    val settled = converge(CustomFormation(), fiveRobots, _ => withSpec(onAnchor))
    val gap = ShapeFormation.clearance(0.1, 0.05)
    settled.values.toList.combinations(2).foreach { case List(a, b) =>
      assert(math.hypot(a._1 - b._1, a._2 - b._2) > gap / 2, s"$a and $b ended up on top of each other")
    }
  }

  test("a rotating geometry keeps its radii as the phase advances") {
    // Rounds run far faster than real time, so sample the layout directly at two phases.
    val formation = geometry(spinningSource) match
      case CustomSpec.Geometry(formation) => formation
      case other => fail(s"expected a geometry but got $other")
    val layout = CustomFormation.layout(formation, 6, _ => None, 1.0, 1.5, 0.3)
    val at = (phase: Double) =>
      Fuel.metered(Fuel.Budget.Default)(layout(phase)).toOption.flatten.getOrElse(fail("the geometry produced no slots"))
    val still = at(0.0)
    val turned = at(math.Pi / 3)
    still.zip(turned).foreach { case (before, after) =>
      assertEqualsDouble(math.hypot(after._1, after._2), math.hypot(before._1, before._2), 1e-9)
      assert(math.hypot(after._1 - before._1, after._2 - before._2) > 1e-3, "the ring did not turn")
    }
  }

  test("a geometry reads the operator's live parameters") {
    val tunable = geometry("""Formation(ctx => ring(ctx.count, ctx.param("radius", 0.3)))""")
    val small = settle(CustomFormation(), fiveRobots, _ => withSpec(tunable, Map(CircleFormation.RADIUS_SENSING -> 0.4)))
    val large = settle(CustomFormation(), fiveRobots, _ => withSpec(tunable, Map(CircleFormation.RADIUS_SENSING -> 0.9)))
    assertNotEquals(small, large, "changing the radius changed nothing")
  }

  private val circleProgram =
    """|new ShapeFormation:
       |  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
       |    val floor = ShapeFormation.minRingRadius(ctx.count, clearance)
       |    ShapeFormation.ring(ctx.count, 0.0)(_ => math.max(sense[Double]("radius"), floor))
       |""".stripMargin

  /** The registry the runtime ships, rebuilt so this suite has VMs of its own. */
  private def registry = AllDemoToLoad("circleShape" -> CircleFormation(), "custom" -> CustomFormation(), "stop" -> Stop())

  test("a designed program takes the fleet over exactly like its built-in twin") {
    // Compiled once, as MqttProvider does: every device must hold the very same program.
    val designedSpec = program(circleProgram)
    val designed = settle(registry, fiveRobots, _ => withSpec(designedSpec))
    val builtIn = settle(registry, fiveRobots, _ => withSpec(CustomSpec.Absent) + (BaseDemo.Program -> "circleShape"))
    assertEquals(designed.keySet, fiveRobots.keySet, "every robot must get a command")
    assertEquals(designed, builtIn)
  }

  test("replacing one designed program with another does not corrupt the round") {
    val first = program(circleProgram)
    val second = program("new Stop()")
    val orchestrator = orchestratorFor(registry)
    List(first, second, first).foreach { spec =>
      (1 to 10).foreach { round =>
        val actuations = orchestrator.tick(FakeWorld(fiveRobots, _ => withSpec(spec)))
        assertEquals(actuations.size, fiveRobots.size, s"a robot lost its round at $round")
      }
      // Half the fleet still on the other program for one tick.
      val mixed = orchestrator.tick(FakeWorld(fiveRobots, id => withSpec(if id % 2 == 0 then spec else second)))
      assertEquals(mixed.size, fiveRobots.size, "a robot lost its round across the switch")
    }
  }

  test("a designed program that never terminates stops the fleet instead of hanging the loop") {
    val endless =
      """|new ShapeFormation:
         |  def spin(k: Long): Long = spin(k + 1)
         |  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
         |    ShapeFormation.ring(ctx.count, 0.0)(_ => spin(0).toDouble)
         |""".stripMargin
    val endlessSpec = program(endless)
    val orchestrator = orchestratorFor(registry, halt = Some(Actuation.Stop))
    val actuations = (1 to 5).map(_ => orchestrator.tick(FakeWorld(fiveRobots, _ => withSpec(endlessSpec)))).last
    assertEquals(actuations.keySet, fiveRobots.keySet)
    // Only the root lays out slots, so it is the one that runs out, and it is halted rather than
    // left to hang the tick for everyone.
    assertEquals(actuations(1), Actuation.Stop)
  }

  test("a program held under the custom name falls back to the ring when it is not a program") {
    val actuations = settle(registry, fiveRobots, _ => withSpec(CustomSpec.Invalid("nope")))
    assertEquals(actuations.keySet, fiveRobots.keySet)
  }
