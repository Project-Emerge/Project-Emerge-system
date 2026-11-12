package it.unibo.demo.formation

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.formation.Fuel.{Budget, OutOfFuel}
import it.unibo.demo.formation.dsl.{Formation, FormationContext, Point}
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.{BaseDemo, CircleFormation, FormationDefaults}
import it.unibo.utils.Position.given

/**
 * Formations written as Scala source and compiled while the runtime is running.
 *
 * The compiler is shared across the suite, as it would be in the runtime: building one costs
 * seconds, reusing it costs a fraction of that.
 */
class FormationCompilerSuite extends munit.FunSuite:

  override val munitTimeout = scala.concurrent.duration.Duration(120, "s")

  private val compiler = FormationCompiler()

  private def geometry(source: String) = compiler.compile(SnippetKind.Geometry, source)

  /** Compiled code only runs under a meter, as it will in the runtime. */
  private def evaluate(formation: Formation, ctx: FormationContext): Either[OutOfFuel, List[Point]] =
    Fuel.metered(Budget.Default)(formation.slots(ctx))

  /** Compiled and evaluated; what went wrong, if anything, as text. */
  private def slotsOf(source: String, ctx: FormationContext): Either[String, List[Point]] =
    geometry(source).left.map(_.mkString("; ")).flatMap(evaluate(_, ctx).left.map(_.getMessage))

  private def radii(slots: List[Point]): List[Double] = slots.map((x, y) => math.hypot(x, y))

  private def context(count: Int, phase: Double = 0.0, params: Map[String, Double] = Map.empty) =
    FormationContext(count, phase, params)

  test("a geometry snippet becomes a formation with one slot per robot") {
    val slots = slotsOf("Formation { ctx => ring(ctx.count, 0.5) }", context(4))
    assertEquals(slots.map(_.size), Right(4))
    slots.foreach(points => radii(points).foreach(r => assertEqualsDouble(r, 0.5, 1e-9)))
  }

  test("a snippet reads the operator's parameters, falling back to its own defaults") {
    val source = """Formation { ctx => ring(ctx.count, ctx.param("radius", 0.3)) }"""
    val centimetres = (slots: List[Point]) => radii(slots).map(r => math.round(r * 100)).distinct
    assertEquals(slotsOf(source, context(3, params = Map("radius" -> 0.8))).map(centimetres), Right(List(80L)))
    assertEquals(slotsOf(source, context(3)).map(centimetres), Right(List(30L)))
  }

  test("a snippet that uses the shared phase moves with it") {
    val source = "Formation { ctx => ring(ctx.count, 0.5).rotated(ctx.phase) }"
    val atRest = slotsOf(source, context(3))
    val turned = slotsOf(source, context(3, phase = math.Pi / 2))
    assert(atRest.isRight && atRest != turned, s"expected the slots to move, got $atRest and $turned")
  }

  test("ordinary Scala a formation legitimately needs gets through the checks and the meter") {
    val source =
      """|{
         |  val petals = 5
         |  def bump(angle: Double): Double = 0.15 * math.abs(math.sin(petals * angle / 2))
         |  Formation { ctx =>
         |    val step = 2 * Math.PI / ctx.count
         |    val flower = (0 until ctx.count).toList.map { i =>
         |      val angle = i * step + ctx.phase / 4
         |      val r = ctx.param("radius", 0.6) + bump(angle)
         |      (r * math.sin(angle), r * math.cos(angle))
         |    }
         |    val triangle = outline(ctx.count, closed = true)((0.0, 0.9), (0.7, -0.5), (-0.7, -0.5))
         |    val chosen = Option.when(ctx.count > 6)(flower).getOrElse(triangle)
         |    chosen.collect { case (x, y) if x.isFinite && y.isFinite => (x, y) }.scaled(1.0)
         |  }
         |}""".stripMargin
    val slots = slotsOf(source, context(8, phase = 1.0))
    assertEquals(slots.map(_.size), Right(8), s"got $slots")
  }

  test("a type error comes back on the line the author wrote it") {
    val source =
      """|Formation { ctx =>
         |  ring(ctx.count, "half a metre")
         |}""".stripMargin
    geometry(source) match
      case Left(problems) =>
        assert(problems.exists(_.line == 2), s"expected an error on line 2, got $problems")
      case Right(_) => fail("a String radius must not compile")
  }

  /** Each of these must be refused with a message naming what it reached for. */
  private val forbidden: List[(String, String, String)] = List(
    ("terminating the process", "{ System.exit(1); Formation(_ => Nil) }", "java.lang.System"),
    (
      "touching the filesystem",
      """{ java.nio.file.Files.delete(java.nio.file.Path.of("/tmp/x")); Formation(_ => Nil) }""",
      "java.nio.file"
    ),
    ("reflection through getClass", "{ val l = this.getClass.getClassLoader; Formation(_ => Nil) }", "getClass"),
    ("starting a thread", "{ new Thread(() => ()).start(); Formation(_ => Nil) }", "java.lang.Thread"),
    (
      "running a process",
      """{ import scala.sys.process.*; "rm -rf /".!; Formation(_ => Nil) }""",
      "scala.sys.process"
    ),
    ("an unchecked cast", "Formation(_ => List((1.0, 2.0)).asInstanceOf[List[(Double, Double)]])", "asInstanceOf"),
    ("a while loop", "{ var k = 0; while k < 10 do k += 1; Formation(_ => Nil) }", "loop"),
    ("printing", """{ println("hi"); Formation(_ => Nil) }""", "scala.Predef.println"),
    (
      "escaping the wrapper's braces",
      "Formation(_ => Nil) }; val leak = System.getenv(); def other: Formation = { Formation(_ => Nil)",
      "java.lang.System"
    ),
    // Each of the following could swallow running out of fuel, hide from the meter, or turn a
    // single number into a collection of that size without calling back into metered code.
    ("catching exceptions", "Formation(ctx => try ring(ctx.count, 0.5) catch case _: Throwable => Nil)", "try"),
    ("an inline definition", "{ inline def r = 0.5; Formation(ctx => ring(ctx.count, r)) }", "inline"),
    ("the standard library's Int ranges", "Formation(ctx => intWrapper(0).until(9).toList.map(_ => (0.0, 1.0)))", "scala.runtime.RichInt.until"),
    ("a range factory", "Formation(_ => List.range(0, 9).map(_ => (0.0, 1.0)))", "range"),
    ("padding to a length", "Formation(_ => List.empty[Point].padTo(9, (0.0, 1.0)))", "padTo"),
    ("permutations", "Formation(ctx => ring(ctx.count, 0.5).permutations.next())", "permutations"),
    ("flattening", "Formation(ctx => List(ring(ctx.count, 0.5)).flatten)", "flatten"),
    ("arbitrary-precision numbers", "Formation(ctx => ring(ctx.count, BigInt(2).pow(9).toDouble))", "scala.math.BigInt"),
    ("long ranges", "Formation(_ => scala.collection.immutable.Range.Long(0, 9, 1).toList.map(_ => (0.0, 1.0)))", "Range.Long")
  )

  forbidden.foreach { (what, source, named) =>
    test(s"the sandbox refuses $what") {
      geometry(source) match
        case Left(problems) =>
          assert(problems.exists(_.message.contains(named)), s"expected a mention of `$named`, got $problems")
        case Right(_) => fail(s"$what must not compile")
    }
  }

  test("a refused snippet never runs, not even the part before the offence") {
    val marker = "formation.compiler.suite.ran"
    val source = s"""{ System.setProperty("$marker", "yes"); System.exit(1); Formation(_ => Nil) }"""
    assert(geometry(source).isLeft)
    assertEquals(Option(System.getProperty(marker)), None)
  }

  /** What every runaway below must come to: a clean refusal, never a hang or a crash. */
  private def assertRunsOut(source: String, mentioning: String): Unit =
    slotsOf(source, context(4)) match
      case Left(reason) => assert(reason.contains(mentioning), s"expected a mention of `$mentioning`, got: $reason")
      case Right(slots) => fail(s"expected the formation to run out, got $slots")

  test("a formation that never terminates runs out of fuel instead") {
    // A tail call compiles to a loop: no allowlist can see this one coming.
    assertRunsOut(
      """|{
         |  def spin(k: Long): Long = if k >= 0 then spin(k + 1) else k
         |  Formation { ctx => ring(ctx.count, spin(0).toDouble) }
         |}""".stripMargin,
      "steps"
    )
  }

  test("fuel runs out inside a by-name argument too") {
    assertRunsOut("Formation { ctx => List.fill(Int.MaxValue)((0.0, 1.0)).take(ctx.count) }", "steps")
  }

  test("a range costs fuel for every number it holds") {
    assertRunsOut("Formation { ctx => (0 until Int.MaxValue).toList.take(ctx.count).map(_ => (0.0, 1.0)) }", "steps")
  }

  test("the formation DSL's own builders cost fuel for every slot they lay out") {
    assertRunsOut("Formation { ctx => ring(Int.MaxValue, 0.5).take(ctx.count) }", "steps")
  }

  test("a formation that keeps doubling its data runs out of memory") {
    assume(Fuel.measuresAllocation, "this JVM does not report per-thread allocation")
    assertRunsOut(
      """|{
         |  def grow(xs: List[Point], k: Int): List[Point] = if k == 0 then xs else grow(xs ++ xs, k - 1)
         |  Formation { ctx => grow(List((0.0, 1.0)), 40).take(ctx.count) }
         |}""".stripMargin,
      "bytes"
    )
  }

  test("a formation that recurses past the stack fails cleanly") {
    assertRunsOut(
      """|{
         |  def deep(k: Long): Long = if k == 0 then 0 else 1 + deep(k - 1)
         |  Formation { ctx => ring(ctx.count, deep(Long.MaxValue).toDouble) }
         |}""".stripMargin,
      "deep"
    )
  }

  test("compiled code refuses to run outside a meter") {
    geometry("Formation { ctx => ring(ctx.count, 0.5) }") match
      case Left(problems) => fail(s"expected the formation to compile, got $problems")
      case Right(formation) => intercept[OutOfFuel](formation.slots(context(4)))
  }

  /** A fully connected fleet, as in AggregateFormationSuite. */
  private final class FakeWorld(val positions: Map[Int, (Double, Double)], val config: Map[String, Any])
      extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = config + (BaseDemo.Orientation -> 0.0)
    override def neighbors(id: Int): Set[Int] = positions.keySet

  private val fleet = Map(1 -> (0.0, 0.0), 2 -> (0.5, 0.0), 3 -> (0.0, 0.5), 4 -> (0.5, 0.5))
  private val circleConfig: Map[String, Any] = FormationDefaults.All + (CircleFormation.RADIUS_SENSING -> 0.5)

  /** Each round metered as a whole, which is all a compiled-in program needs: it never ticks. */
  private def actuationsAfter(program: BaseDemo, rounds: Int = 30): Map[Int, Actuation] =
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](program)
    (1 to rounds).foldLeft(Map.empty[Int, Actuation]) { (_, _) =>
      Fuel.metered(Budget.Default)(orchestrator.tick(FakeWorld(fleet, circleConfig))).getOrElse(Map.empty)
    }

  test("a whole aggregate program compiles against ScaFi and drives the fleet like its built-in twin") {
    val source =
      """|new ShapeFormation:
         |  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
         |    val floor = ShapeFormation.minRingRadius(ctx.count, clearance)
         |    ShapeFormation.ring(ctx.count, 0.0)(_ => math.max(sense[Double]("radius"), floor))
         |""".stripMargin
    compiler.compile(SnippetKind.Aggregate, source) match
      case Left(problems) => fail(s"expected the program to compile, got $problems")
      case Right(program) =>
        val compiled = actuationsAfter(program)
        assertEquals(compiled.keySet, fleet.keySet, "every robot must get a command")
        assertEquals(compiled, actuationsAfter(CircleFormation()))
  }

  test("an aggregate program is held to the same sandbox") {
    val source =
      """|new ShapeFormation:
         |  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
         |    System.exit(1)
         |    Nil
         |""".stripMargin
    compiler.compile(SnippetKind.Aggregate, source) match
      case Left(problems) => assert(problems.exists(p => p.line == 3 && p.message.contains("java.lang.System")), problems)
      case Right(_) => fail("System.exit must not compile")
  }
