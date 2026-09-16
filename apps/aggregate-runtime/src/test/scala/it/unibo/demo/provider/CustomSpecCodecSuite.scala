package it.unibo.demo.provider

import it.unibo.demo.scenarios.{CustomSlots, CustomSpec, Formula, ShapeFormation, SlotContext}

/**
 * Covers the only place an untrusted payload becomes a formation.
 *
 * The property that matters most is leniency in the right direction: `/config/formation` is
 * retained, so a payload that fails hard leaves the fleet stuck on its previous shape with no
 * live publisher around to correct it. One bad field must cost only that field.
 */
class CustomSpecCodecSuite extends munit.FunSuite:

  private def decode(json: String): CustomSpec = CustomSpecCodec.decode(ujson.read(json))

  private def fromCommand(json: String): Option[CustomSpec] =
    CustomSpecCodec.fromCommand(ujson.read(json))

  private def reasonOf(spec: CustomSpec): String = spec match
    case CustomSpec.Invalid(reason) => reason
    case other => fail(s"expected a rejection but got $other")

  private def evaluate(formula: Formula, i: Double, n: Double, t: Double = 0.0): Double =
    Formula.evaluate(formula, i, n, t)

  test("a points payload decodes into a points spec") {
    decode("""{"kind":"points","points":[[0,0.5],[0.5,0],[-0.5,0]],"closed":true}""") match
      case CustomSpec.Points(points, closed) =>
        assertEquals(points, List((0.0, 0.5), (0.5, 0.0), (-0.5, 0.0)))
        assertEquals(closed, true)
      case other => fail(s"expected a points spec but got $other")
  }

  test("an unclosed points payload defaults to an open path") {
    decode("""{"kind":"points","points":[[0,0.5],[0.5,0]]}""") match
      case CustomSpec.Points(_, closed) => assertEquals(closed, false)
      case other => fail(s"expected a points spec but got $other")
  }

  test("a cartesian payload decodes into compiled formulas that evaluate") {
    // Asserting on the evaluated values, not just the shape, covers the wiring end to end.
    decode("""{"kind":"cartesian","x":"0.2*i","y":"0.1*n"}""") match
      case CustomSpec.Cartesian(x, y) =>
        assertEqualsDouble(evaluate(x, 3.0, 5.0), 0.6, 1e-9)
        assertEqualsDouble(evaluate(y, 3.0, 5.0), 0.5, 1e-9)
      case other => fail(s"expected a cartesian spec but got $other")
  }

  test("a polar payload decodes into compiled formulas that evaluate") {
    decode("""{"kind":"polar","r":"0.5","theta":"2*pi*i/n"}""") match
      case CustomSpec.Polar(r, theta) =>
        assertEqualsDouble(evaluate(r, 1.0, 4.0), 0.5, 1e-9)
        assertEqualsDouble(evaluate(theta, 1.0, 4.0), math.Pi / 2, 1e-9)
      case other => fail(s"expected a polar spec but got $other")
  }

  test("the kind can be inferred when the author forgot it") {
    assert(decode("""{"points":[[0,0.5]]}""").isInstanceOf[CustomSpec.Points])
    assert(decode("""{"x":"i","y":"0"}""").isInstanceOf[CustomSpec.Cartesian])
    assert(decode("""{"r":"0.5","theta":"i"}""").isInstanceOf[CustomSpec.Polar])
  }

  test("an unknown kind is rejected with a reason that lists the kinds") {
    val reason = reasonOf(decode("""{"kind":"spline","points":[[0,0.5]]}"""))
    assert(reason.contains("points"), reason)
    assert(reason.contains("cartesian"), reason)
    assert(reason.contains("polar"), reason)
  }

  test("an object that names no geometry at all is rejected") {
    val reason = reasonOf(decode("""{"label":"Star"}"""))
    assert(reason.contains("points"), reason)
  }

  test("a payload with no custom key leaves the previous spec alone") {
    // The same "a message that omits a key changes nothing about that key" semantics `params`
    // has, and what an operator expects when they switch programs and back.
    assertEquals(
      fromCommand("""{"program":"circleShape","leaderId":null,"anchor":"auto","params":{}}"""),
      None
    )
  }

  test("an explicit null clears the spec") {
    assertEquals(
      fromCommand("""{"program":"custom","leaderId":null,"anchor":"auto","params":{},"custom":null}"""),
      Some(CustomSpec.Absent)
    )
  }

  test("unknown keys are ignored rather than failing the message") {
    decode("""{"kind":"points","points":[[0,0.5]],"label":"Star","wobble":7,"extra":{"a":1}}""") match
      case CustomSpec.Points(points, _) => assertEquals(points, List((0.0, 0.5)))
      case other => fail(s"expected a points spec but got $other")
  }

  test("a malformed point is dropped and the rest survive") {
    decode("""{"kind":"points","points":[[0,1],["a","b"],[2],[3,4,5],{},null,[0.2,0.3]]}""") match
      // The three-element entry keeps its first two coordinates; the rest carry no usable pair.
      case CustomSpec.Points(points, _) => assertEquals(points, List((0.0, 1.0), (3.0, 4.0), (0.2, 0.3)))
      case other => fail(s"expected a points spec but got $other")
  }

  test("a non-finite coordinate is dropped at the edge") {
    // A literal too large for a double parses to an infinity, which is the only route by which
    // JSON can carry a non-finite number at all.
    decode("""{"kind":"points","points":[[1e400,0],[0.2,0.3]]}""") match
      case CustomSpec.Points(points, _) => assertEquals(points, List((0.2, 0.3)))
      case other => fail(s"expected a points spec but got $other")
  }

  test("a points payload with nothing usable is rejected with a reason") {
    assert(reasonOf(decode("""{"kind":"points","points":[]}""")).contains("custom.points"))
    assert(reasonOf(decode("""{"kind":"points","points":[["a","b"],{}]}""")).contains("custom.points"))
    assert(reasonOf(decode("""{"kind":"points","points":"circle"}""")).contains("custom.points"))
  }

  test("a point list longer than the limit is truncated") {
    val many = (0 until 1000).map(k => s"[0.$k,0.1]").mkString("[", ",", "]")
    decode(s"""{"kind":"points","points":$many}""") match
      case CustomSpec.Points(points, _) => assertEquals(points.size, CustomSpec.MaxPoints)
      case other => fail(s"expected a points spec but got $other")
  }

  test("a formula that does not compile is reported by field and does not fail the message") {
    assert(reasonOf(decode("""{"kind":"cartesian","x":"sin(","y":"0"}""")).startsWith("custom.x:"))
    assert(reasonOf(decode("""{"kind":"cartesian","x":"0","y":"System"}""")).startsWith("custom.y:"))
    assert(reasonOf(decode("""{"kind":"polar","r":"0.5","theta":"foo(1)"}""")).startsWith("custom.theta:"))
  }

  test("a formula field that is not a string is rejected naming both fields") {
    val reason = reasonOf(decode("""{"kind":"cartesian","x":5,"y":"0"}"""))
    assert(reason.contains("custom.x") && reason.contains("custom.y"), reason)
    assert(reasonOf(decode("""{"kind":"polar","r":"0.5"}""")).contains("custom.theta"))
  }

  test("a payload that is not an object is rejected rather than thrown") {
    List("5", "\"circle\"", "[]", "true").foreach { json =>
      assert(reasonOf(decode(json)).contains("JSON object"), json)
    }
  }

  test("a duplicated end point closes the path") {
    // An author who repeated the first vertex meant a loop; keeping it would put two robots on
    // the seam.
    decode("""{"kind":"points","points":[[0,1],[1,0],[0,1]]}""") match
      case CustomSpec.Points(points, closed) =>
        assertEquals(points, List((0.0, 1.0), (1.0, 0.0)))
        assertEquals(closed, true)
      case other => fail(s"expected a points spec but got $other")
  }

  test("a decoded polar spec reproduces the built-in ring") {
    // The convention every formula author will rely on, checked through the codec rather than
    // only through the geometry, so a change to either side is caught.
    val spec = decode("""{"kind":"polar","r":"0.5","theta":"2*pi*i/n"}""")
    val placed = CustomSlots
      .slotsFor(spec, SlotContext(6, 0.0, 0.0), 1.0, 1.5, 0.3)
      .getOrElse(fail("the spec produced no slots"))
    ShapeFormation.ring(6, 0.0)(_ => 0.5).zip(placed).foreach { case (expected, got) =>
      assertEqualsDouble(got._1, expected._1, 1e-9)
      assertEqualsDouble(got._2, expected._2, 1e-9)
    }
  }

  test("decoding is total") {
    val hostile = List(
      "5", "null", "[]", "{}", "\"\"", "true",
      """{"kind":7}""",
      """{"kind":"points","points":{}}""",
      """{"kind":"points","points":[[[[1]]]]}""",
      """{"kind":"cartesian","x":null,"y":null}""",
      s"""{"kind":"cartesian","x":"${"1+" * 400}1","y":"0"}""",
      s"""{"kind":"points","points":[[0,1]],"closed":"${"x" * 1000}"}""",
      """{"kind":"polar","r":{"a":1},"theta":[1,2]}""",
      """{"points":[[1e400,1e400]]}"""
    )
    hostile.foreach { json =>
      // No assertion on which spec: the promise is only that a CustomSpec comes back.
      val spec = decode(json)
      assert(spec != null, json)
    }
  }
