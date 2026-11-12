package it.unibo.demo.formation

import it.unibo.demo.formation.dsl.FormationContext

/**
 * Spike-only: how long the embedded compiler takes, cold and warm, in the JVM it ships in.
 *
 * `java -cp app.jar it.unibo.demo.formation.FormationCompilerBenchmark [warm-compiles]`
 */
object FormationCompilerBenchmark:

  private def geometry(k: Int): String =
    s"""|Formation { ctx =>
        |  val wobble = 0.05 * math.sin(3 * ctx.phase + $k)
        |  ring(ctx.count, ctx.param("radius", 0.6) + wobble).rotated(ctx.phase / 8)
        |}""".stripMargin

  private val aggregate: String =
    """|new ShapeFormation:
       |  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
       |    ShapeFormation.ring(ctx.count, 0.0)(_ => math.max(sense[Double]("radius"), clearance))
       |""".stripMargin

  private def timed[A](body: => A): (A, Double) =
    val start = System.nanoTime()
    val result = body
    (result, (System.nanoTime() - start) / 1e6)

  private def heapMb(): Double =
    System.gc()
    val runtime = Runtime.getRuntime
    (runtime.totalMemory() - runtime.freeMemory()) / 1e6

  private def stats(label: String, millis: Seq[Double]): Unit =
    val sorted = millis.sorted
    val median = sorted(sorted.size / 2)
    println(f"$label%-34s n=${millis.size}%3d  median ${median}%8.1f ms  min ${sorted.head}%8.1f  max ${sorted.last}%8.1f")

  def main(args: Array[String]): Unit =
    val warmCount = args.headOption.flatMap(_.toIntOption).getOrElse(30)
    println(s"java ${System.getProperty("java.version")} (${System.getProperty("java.vm.name")})")
    val heapBefore = heapMb()

    val compiler = FormationCompiler()
    val (first, cold) = timed(compiler.compile(SnippetKind.Geometry, geometry(0)))
    require(first.isRight, s"cold compile failed: $first")
    stats("cold (first compile, new JVM)", Seq(cold))

    val warm = (1 to warmCount).map(k => timed(compiler.compile(SnippetKind.Geometry, geometry(k)))._2)
    stats("warm geometry (shared compiler)", warm)

    val fresh = (1 to 5).map(k => timed(FormationCompiler().compile(SnippetKind.Geometry, geometry(1000 + k)))._2)
    stats("fresh compiler each time (JIT warm)", fresh)

    val (program, aggregateMillis) = timed(compiler.compile(SnippetKind.Aggregate, aggregate))
    require(program.isRight, s"aggregate compile failed: $program")
    stats("warm aggregate program (ScaFi)", Seq(aggregateMillis))

    val refused = (1 to 10).map(_ => timed(compiler.compile(SnippetKind.Geometry, "{ System.exit(1); Formation(_ => Nil) }")))
    require(refused.forall(_._1.isLeft), "System.exit must be refused")
    stats("refused by the sandbox", refused.map(_._2))

    val (typeError, typeErrorMillis) = timed(compiler.compile(SnippetKind.Geometry, "Formation { ctx => ring(ctx.count, \"x\") }"))
    stats("type error", Seq(typeErrorMillis))
    typeError.left.foreach(_.foreach(p => println(s"    -> line ${p.line}, column ${p.column}: ${p.message.linesIterator.next()}")))

    first.foreach { formation =>
      val ctx = FormationContext(12, 1.0, Map("radius" -> 0.7))
      val evaluate = (phase: Double) => Fuel.metered(Fuel.Budget.Default)(formation.slots(ctx.copy(phase = phase)))
      (1 to 20_000).foreach(k => evaluate(k * 1e-3))
      val rounds = 100_000
      val (_, millis) = timed((1 to rounds).foreach(k => evaluate(k * 1e-3)))
      println(f"metered evaluation, 12 slots          ${millis * 1000 / rounds}%8.2f us per call")
    }
    compiler
      .compile(SnippetKind.Geometry, "{ def spin(k: Long): Long = spin(k + 1); Formation(ctx => ring(ctx.count, spin(0).toDouble)) }")
      .foreach { endless =>
        val run = () => Fuel.metered(Fuel.Budget.Default)(endless.slots(FormationContext(12, 0.0, Map.empty)))
        (1 to 20).foreach(_ => run())
        val stops = (1 to 20).map(_ => timed(run()))
        require(stops.forall(_._1.isLeft), "an endless formation must run out")
        stats(s"endless formation, ${Fuel.Budget.Default.steps} steps", stops.map(_._2))
      }

    println(f"heap: before ${heapBefore}%.0f MB, after ${warmCount + 18} compiles ${heapMb()}%.0f MB")
    val soak = (1 to warmCount).map(k => timed(compiler.compile(SnippetKind.Geometry, geometry(5000 + k)))._2)
    stats("warm geometry, second batch", soak)
    println(f"heap after ${2 * warmCount + 18} compiles: ${heapMb()}%.0f MB")
