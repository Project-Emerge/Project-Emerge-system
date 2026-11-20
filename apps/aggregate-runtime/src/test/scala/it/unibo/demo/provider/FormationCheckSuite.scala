package it.unibo.demo.provider

import it.unibo.demo.formation.SharedCompiler

/**
 * The check an author runs before publishing: compile, then lay the geometry out for the fleet
 * that is actually there, so every way it could fail on the floor is reported up front.
 */
class FormationCheckSuite extends munit.FunSuite:

  override val munitTimeout = scala.concurrent.duration.Duration(120, "s")

  private def request(source: String, kind: CustomKind = CustomKind.Formation, slots: Int = 5) =
    FormationCheck.Request("req-1", kind, source, slots, Map.empty)

  private def check(source: String, kind: CustomKind = CustomKind.Formation, slots: Int = 5) =
    FormationCheck.run(SharedCompiler.instance, request(source, kind, slots))

  test("a geometry that compiles and lays out cleanly comes back with a preview of its slots") {
    val result = check("Formation(ctx => ring(ctx.count, 0.6))")
    assertEquals(result.problems, Nil)
    assertEquals(result.preview.map(_.size), Some(5))
    result.preview.foreach(_.foreach((x, y) => assertEqualsDouble(math.hypot(x, y), 0.6, 1e-9)))
  }

  test("a compile error comes back with its position and no preview") {
    val result = check("Formation { ctx =>\n  ring(ctx.count, \"far\")\n}")
    assert(result.problems.exists(_.line == 2), result.problems)
    assertEquals(result.preview, None)
  }

  test("a geometry that lays out the wrong number of slots is reported, naming ctx.count") {
    val result = check("Formation(_ => List((0.5, 0.5), (0.0, 0.7)))")
    assert(result.problems.exists(p => p.message.contains("2 slots") && p.message.contains("ctx.count")), result.problems)
  }

  test("a geometry that runs out of fuel at any phase of its cycle is reported") {
    // Fine at rest, endless once the shared phase moves: only sampling the cycle catches it.
    val source =
      """|{
         |  def spin(k: Long): Long = spin(k + 1)
         |  Formation(ctx => ring(ctx.count, if ctx.phase > 1.0 then spin(0).toDouble else 0.5))
         |}""".stripMargin
    assert(check(source).problems.exists(_.message.contains("steps")), check(source).problems)
  }

  test("a geometry that throws is reported with what it threw") {
    val result = check("Formation(ctx => List.empty[Point].head :: Nil)")
    assert(result.problems.exists(_.message.contains("NoSuchElementException")), result.problems)
  }

  test("a program is compiled and has no preview") {
    val result = check("new Stop()", kind = CustomKind.Program)
    assertEquals(result.problems, Nil)
    assertEquals(result.preview, None)
  }

  test("requests are read leniently and answered on a topic of their own") {
    val parsed = FormationCheck.parse(ujson.read("""{"id":"abc-1","kind":"formation","source":"x","slots":4,"params":{"radius":0.5,"bad":"no"}}"""))
    assertEquals(parsed, Right(FormationCheck.Request("abc-1", CustomKind.Formation, "x", 4, Map("radius" -> 0.5))))
    assertEquals(FormationCheck.responseTopic("abc-1"), "/formation/compile/abc-1")
  }

  test("a request whose id could escape its response topic is refused") {
    List("a/b", "+", "#", "", "x" * 65).foreach { id =>
      assert(FormationCheck.parse(ujson.Obj("id" -> id, "kind" -> "formation", "source" -> "x")).isLeft, id)
    }
  }

  test("an answer is encoded as the dashboard reads it") {
    val encoded = FormationCheck.encode(check("Formation(ctx => ring(ctx.count, 0.6))", slots = 2))
    assertEquals(encoded("id").str, "req-1")
    assertEquals(encoded("ok").bool, true)
    assertEquals(encoded("problems").arr.size, 0)
    assertEquals(encoded("preview").arr.map(_.arr.size).toList, List(2, 2))
  }
