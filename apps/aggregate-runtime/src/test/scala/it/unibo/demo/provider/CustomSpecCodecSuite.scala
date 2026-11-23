package it.unibo.demo.provider

import it.unibo.demo.formation.{FormationCompiler, SharedCompiler}
import it.unibo.demo.formation.dsl.FormationContext
import it.unibo.demo.formation.Fuel
import it.unibo.demo.scenarios.CustomSpec

/**
 * Covers the only place an untrusted payload becomes a formation.
 *
 * Leniency still matters in the right direction: `/config/formation` is retained, so a payload
 * that fails hard leaves the fleet stuck on its previous shape with no live publisher around to
 * correct it. A bad `custom` must cost only `custom`, and say why.
 */
class CustomSpecCodecSuite extends munit.FunSuite:

  override val munitTimeout = scala.concurrent.duration.Duration(120, "s")

  private def read(json: String): CustomRequest = CustomSpecCodec.read(ujson.read(json))

  private def fromCommand(json: String): Option[CustomRequest] = CustomSpecCodec.fromCommand(ujson.read(json))

  private def compile(request: CustomRequest): CustomSpec = CustomSpecCodec.compile(SharedCompiler.instance, request)

  private def reasonOf(request: CustomRequest): String = request match
    case CustomRequest.Rejected(reason) => reason
    case other => fail(s"expected a rejection but got $other")

  test("a formation payload asks for its source to be compiled as geometry") {
    assertEquals(
      read("""{"kind":"formation","source":"Formation(ctx => ring(ctx.count, 0.5))","label":"Ring"}"""),
      CustomRequest.Compile(CustomKind.Formation, "Formation(ctx => ring(ctx.count, 0.5))")
    )
  }

  test("a program payload asks for its source to be compiled as an aggregate program") {
    assertEquals(
      read("""{"kind":"program","source":"new Stop()"}"""),
      CustomRequest.Compile(CustomKind.Program, "new Stop()")
    )
  }

  test("the formats Scala replaced are rejected by name, not silently ignored") {
    List(
      """{"kind":"points","points":[[0,0.5],[0.5,0]]}""",
      """{"kind":"cartesian","x":"i","y":"0"}""",
      """{"kind":"polar","r":"0.5","theta":"i"}"""
    ).foreach { json =>
      assert(reasonOf(read(json)).contains("formation, program"), json)
    }
  }

  test("a payload without source, or with source of the wrong type, says what is missing") {
    assert(reasonOf(read("""{"kind":"formation"}""")).contains("custom.source"))
    assert(reasonOf(read("""{"kind":"formation","source":42}""")).contains("custom.source"))
    assert(reasonOf(read("""{"kind":"formation","source":""}""")).contains("custom.source"))
  }

  test("source longer than the compiler accepts is rejected before it reaches the compiler") {
    val long = "x" * (FormationCompiler.MaxSourceLength + 1)
    assert(reasonOf(read(s"""{"kind":"formation","source":"$long"}""")).contains(s"${FormationCompiler.MaxSourceLength}"))
  }

  test("an explicit null clears the spec, and an absent key keeps it") {
    assertEquals(fromCommand("""{"program":"custom","custom":null}"""), Some(CustomRequest.Clear))
    assertEquals(fromCommand("""{"program":"circleShape"}"""), None)
  }

  test("nothing a payload can carry makes reading it throw") {
    List("[]", "3", "\"custom\"", "{}", """{"kind":7}""", """{"kind":"formation","source":["a"]}""")
      .foreach(json => assert(read(json).isInstanceOf[CustomRequest.Rejected], json))
  }

  test("a formation that compiles becomes a geometry the runtime can evaluate") {
    compile(CustomRequest.Compile(CustomKind.Formation, "Formation(ctx => ring(ctx.count, 0.5))")) match
      case CustomSpec.Geometry(formation) =>
        assertEquals(Fuel.metered(Fuel.Budget.Default)(formation.slots(FormationContext.fromParams(3, 0.0, Map.empty))).map(_.size), Right(3))
      case other => fail(s"expected a geometry but got $other")
  }

  test("a program that compiles carries an id that changes with its source") {
    val first = compile(CustomRequest.Compile(CustomKind.Program, "new Stop()"))
    val again = compile(CustomRequest.Compile(CustomKind.Program, "new Stop()"))
    val other = compile(CustomRequest.Compile(CustomKind.Program, "new NoOp()"))
    (first, again, other) match
      case (CustomSpec.Program(a, _), CustomSpec.Program(b, _), CustomSpec.Program(c, _)) =>
        assertEquals(a, b)
        assertNotEquals(a, c)
      case unexpected => fail(s"expected three programs but got $unexpected")
  }

  test("a source the compiler refuses becomes a rejection naming the line") {
    val source = "Formation { ctx =>\n  ring(ctx.count, \"far\")\n}"
    compile(CustomRequest.Compile(CustomKind.Formation, source)) match
      case CustomSpec.Invalid(reason) => assert(reason.contains("line 2"), reason)
      case other => fail(s"expected a rejection but got $other")
  }

  test("a cleared or rejected request compiles to nothing at all") {
    assertEquals(compile(CustomRequest.Clear), CustomSpec.Absent)
    assertEquals(compile(CustomRequest.Rejected("why")), CustomSpec.Invalid("why"))
  }

  test("republishing the same source reuses the compiled instance, so devices never misalign") {
    // An operator dragging a slider republishes the same source many times a second. A fresh
    // class each time would put devices holding the old and the new one on different `align` keys.
    val cache = CustomSpecCache(SharedCompiler.instance)
    val request = CustomRequest.Compile(CustomKind.Program, "new Stop()")
    val other = CustomRequest.Compile(CustomKind.Program, "new NoOp()")
    (cache.compile(request), cache.compile(request), cache.compile(other)) match
      case (CustomSpec.Program(_, first), CustomSpec.Program(_, again), CustomSpec.Program(_, different)) =>
        assert(first eq again, "the same source was compiled twice")
        assert(!(different eq first), "a different source was served from the cache")
      case unexpected => fail(s"expected three programs but got $unexpected")
  }

  test("a refusal is not remembered, so a compile that merely timed out gets another try") {
    val cache = CustomSpecCache(SharedCompiler.instance)
    val broken = CustomRequest.Compile(CustomKind.Formation, "Formation(ctx => ring(ctx.count, \"x\"))")
    assert(cache.compile(broken).isInstanceOf[CustomSpec.Invalid])
    assert(cache.compile(broken).isInstanceOf[CustomSpec.Invalid])
  }
