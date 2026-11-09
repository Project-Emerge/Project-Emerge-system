package it.unibo.demo.scenarios

/**
 * Covers the formula evaluator, which is the part of a data-driven formation an untrusted author
 * writes. Two properties matter more than any single behaviour: nothing outside the whitelist can
 * be named, and a formula that parsed can never throw out of a round.
 */
class FormulaSuite extends munit.FunSuite:

  private val tolerance = 1e-9

  private def value(source: String, i: Double = 0.0, n: Double = 1.0, t: Double = 0.0): Double =
    Formula.compile(source) match
      case Right(formula) => Formula.evaluate(formula, i, n, t)
      case Left(reason) => fail(s"expected '$source' to parse, but: $reason")

  private def rejection(source: String): String =
    Formula.parse(source) match
      case Left(reason) => reason
      case Right(_) => fail(s"expected '$source' to be rejected")

  test("a literal, a variable and a constant each evaluate to themselves") {
    assertEqualsDouble(value("1.5"), 1.5, tolerance)
    assertEqualsDouble(value("i", i = 3.0), 3.0, tolerance)
    assertEqualsDouble(value("n", n = 7.0), 7.0, tolerance)
    assertEqualsDouble(value("t", t = 1.25), 1.25, tolerance)
    assertEqualsDouble(value("pi"), math.Pi, tolerance)
    assertEqualsDouble(value("e"), math.E, tolerance)
  }

  test("the four operators respect the usual precedence") {
    assertEqualsDouble(value("1+2*3"), 7.0, tolerance)
    assertEqualsDouble(value("(1+2)*3"), 9.0, tolerance)
    assertEqualsDouble(value("8/4/2"), 1.0, tolerance)
    assertEqualsDouble(value("7%3"), 1.0, tolerance)
    assertEqualsDouble(value("1-2-3"), -4.0, tolerance)
  }

  test("exponentiation is right-associative and binds tighter than unary minus") {
    // The maths convention, and the one a formula's author will assume.
    assertEqualsDouble(value("-2^2"), -4.0, tolerance)
    assertEqualsDouble(value("2^3^2"), 512.0, tolerance)
    // The exponent is a unary, so a negative exponent needs no parentheses.
    assertEqualsDouble(value("2^-1"), 0.5, tolerance)
    assertEqualsDouble(value("--3"), 3.0, tolerance)
    assertEqualsDouble(value("+4"), 4.0, tolerance)
  }

  test("every whitelisted function is callable and computes what it says") {
    // Table-driven over Fn.values so the whitelist and the evaluator cannot drift apart: a new
    // case with no entry here fails the coverage assertion at the end.
    val expectations: Map[Formula.Fn, (String, Double)] = Map(
      Formula.Fn.Sin -> ("sin(0.5)", math.sin(0.5)),
      Formula.Fn.Cos -> ("cos(0.5)", math.cos(0.5)),
      Formula.Fn.Tan -> ("tan(0.5)", math.tan(0.5)),
      Formula.Fn.Asin -> ("asin(0.5)", math.asin(0.5)),
      Formula.Fn.Acos -> ("acos(0.5)", math.acos(0.5)),
      Formula.Fn.Atan -> ("atan(0.5)", math.atan(0.5)),
      Formula.Fn.Atan2 -> ("atan2(1,2)", math.atan2(1.0, 2.0)),
      Formula.Fn.Sqrt -> ("sqrt(9)", 3.0),
      Formula.Fn.Abs -> ("abs(0-2.5)", 2.5),
      Formula.Fn.Floor -> ("floor(2.7)", 2.0),
      Formula.Fn.Ceil -> ("ceil(2.1)", 3.0),
      Formula.Fn.Round -> ("round(2.4)", 2.0),
      Formula.Fn.Exp -> ("exp(1)", math.E),
      Formula.Fn.Log -> ("log(e)", 1.0),
      Formula.Fn.Sign -> ("sign(0-3)", -1.0),
      Formula.Fn.Hypot -> ("hypot(3,4)", 5.0),
      Formula.Fn.Min -> ("min(2,5)", 2.0),
      Formula.Fn.Max -> ("max(2,5)", 5.0)
    )
    expectations.foreach { case (fn, (source, expected)) =>
      assertEqualsDouble(value(source), expected, 1e-9, s"$fn via '$source'")
    }
    assertEquals(expectations.keySet, Formula.Fn.values.toSet)
    assertEquals(Formula.Fn.byName.size, Formula.Fn.values.length)
  }

  test("min and max take more than two arguments") {
    assertEqualsDouble(value("max(1,5,3)"), 5.0, tolerance)
    assertEqualsDouble(value("min(4,2,9,1)"), 1.0, tolerance)
  }

  test("nothing outside the whitelist can be named") {
    List("foo(1)", "System", "println(1)", "sys", "x", "theta", "scala", "Runtime")
      .foreach { source =>
        val reason = rejection(source)
        assert(
          reason.contains("unknown"),
          s"'$source' should be rejected as unknown, but was: $reason"
        )
      }
  }

  test("a call with the wrong number of arguments is rejected") {
    List("sin()", "sin(1,2)", "atan2(1)", "min(1)", "hypot(1,2,3)").foreach(rejection)
    val tooMany = (0 to FormulaLimits.MaxArgs).mkString("max(", ",", ")")
    rejection(tooMany)
  }

  test("unbalanced parentheses and trailing input are rejected") {
    List("(1+2", "1+2)", "1 2", "1 e", "", "+", "*2", "sin", ",", "1,", "1..2", "@").foreach(rejection)
  }

  test("a formula longer than the limit is rejected before it is parsed") {
    val long = "1+" * 400 + "1"
    assert(long.length > FormulaLimits.MaxSourceLength)
    assert(rejection(long).contains(FormulaLimits.MaxSourceLength.toString))
  }

  test("a formula with more operations than the budget is rejected") {
    // Deliberately inside the character limit, so it is the node budget doing the rejecting and
    // not the cheaper length check in front of it. 200 literals plus 199 additions is 399 nodes.
    val many = List.fill(200)("1").mkString("+")
    assert(many.length <= FormulaLimits.MaxSourceLength, s"the probe itself is too long: ${many.length}")
    assert(rejection(many).contains("operations"))
  }

  test("deep nesting is rejected instead of overflowing the stack") {
    // Parentheses cost a stack frame but build no node, so the node budget alone would not save
    // us here. The test completing at all is the assertion: a StackOverflowError is an Error, not
    // an Exception, and would escape the parser's catch.
    val deep = "(" * 5000 + "1" + ")" * 5000
    assert(Formula.parse(deep).isLeft)
    // Even past the character limit it must be a clean rejection rather than a crash.
    assert(Formula.parse("(" * 100000).isLeft)
  }

  test("nesting is bounded even when the source fits the length and node budgets") {
    val depth = FormulaLimits.MaxDepth
    val sources = List(
      "(" * depth + "1" + ")" * depth,
      "+" * depth + "1",
      "2^" * depth + "1",
      "sin(" * depth + "0" + ")" * depth
    )
    sources.foreach { source =>
      assert(source.length <= FormulaLimits.MaxSourceLength)
      assert(rejection(source).contains("nested deeper"))
    }
  }

  test("a domain error evaluates to a non-finite number rather than throwing") {
    List("sqrt(0-1)", "log(0)", "1/0", "0/0", "asin(2)", "acos(2)", "log(0-1)")
      .foreach { source =>
        val result = value(source)
        assert(!result.isFinite, s"'$source' gave the finite $result")
      }
  }

  test("modulo by zero yields NaN rather than throwing") {
    assert(value("5%0").isNaN)
  }

  test("scientific notation parses and does not collide with the constant e") {
    assertEqualsDouble(value("1e3"), 1000.0, tolerance)
    assertEqualsDouble(value("2e-2"), 0.02, tolerance)
    assertEqualsDouble(value("1E2"), 100.0, tolerance)
    assertEqualsDouble(value(".5"), 0.5, tolerance)
    assertEqualsDouble(value("e"), math.E, tolerance)
    assertEqualsDouble(value("e*2"), math.E * 2, tolerance)
    assertEqualsDouble(value("exp(1)"), math.E, tolerance)
    // A digit, a space and an e are two adjacent atoms, not a power of ten.
    rejection("1 e")
  }

  test("incomplete numbers and scientific exponents are rejected") {
    List(".", ".e2", "1e", "1e+", "1E-", "1e 2", "1e+ 2").foreach(rejection)
    assertEqualsDouble(value("1.e+2"), 100.0, tolerance)
    assertEqualsDouble(value(".5e-1"), 0.05, tolerance)
  }

  test("round preserves Double range and rounds ties to even") {
    assertEqualsDouble(value("round(2.5)"), 2.0, tolerance)
    assertEqualsDouble(value("round(3.5)"), 4.0, tolerance)
    assertEqualsDouble(value("round(-2.5)"), -2.0, tolerance)
    assertEquals(value("round(1e100)"), 1e100)
  }

  test("the shared phase is wrapped into one turn") {
    val turn = 2 * math.Pi
    assertEqualsDouble(Formula.wrapPhase(0.0), 0.0, tolerance)
    assertEqualsDouble(Formula.wrapPhase(turn), 0.0, tolerance)
    assertEqualsDouble(Formula.wrapPhase(-math.Pi), math.Pi, tolerance)
    // The reading the runtime actually produces: about 1.9e9 radians since the epoch.
    val wrapped = Formula.wrapPhase(1.9e9)
    assert(wrapped >= 0.0 && wrapped < turn, s"1.9e9 wrapped to $wrapped")
    assertEqualsDouble(Formula.wrapPhase(Double.NaN), 0.0, tolerance)
    assertEqualsDouble(Formula.wrapPhase(Double.PositiveInfinity), 0.0, tolerance)
  }

  test("compiling the same source twice gives the same tree") {
    val first = Formula.compile("2*sin(i)+1")
    val second = Formula.compile("2*sin(i)+1")
    assertEquals(first, second)
    assert(first.isRight)
  }

  test("compiling a rejected source twice reports the same reason") {
    // Failures are memoised too, so a hostile source is only ever parsed once.
    assertEquals(Formula.compile("sin("), Formula.compile("sin("))
    assert(Formula.compile("sin(").isLeft)
  }

  test("the parse cache stays bounded and keeps working") {
    val early = "0.125*i+3"
    assert(Formula.compile(early).isRight)
    (0 until FormulaLimits.MaxCachedFormulas * 3).foreach { k =>
      Formula.compile(s"$k+i")
    }
    assert(
      Formula.cacheSize <= FormulaLimits.MaxCachedFormulas,
      s"cache grew to ${Formula.cacheSize}"
    )
    // Evicted or not, an early source still compiles to the right thing.
    assertEqualsDouble(value(early, i = 8.0), 4.0, tolerance)
  }

  test("whitespace is insignificant") {
    assertEquals(Formula.parse(" 1 + 2 * sin ( 0 ) "), Formula.parse("1+2*sin(0)"))
    assertEqualsDouble(value("\t2\n+\r3 "), 5.0, tolerance)
  }

  test("evaluating a formula is total for every hostile source that parsed") {
    val hostile = List(
      "exp(exp(i))", "1/(i-i)", "0^0", "log(0-1)^0.5", "round(exp(700))",
      "tan(pi/2)", "sqrt(0-i)", "i^i^i", "min(1/0,0/0)", "abs(0-1e300*1e300)"
    )
    hostile.foreach { source =>
      val formula = Formula.compile(source) match
        case Right(parsed) => parsed
        case Left(reason) => fail(s"'$source' should parse, but: $reason")
      (0 until 32).foreach { i =>
        // No assertion on the value: the promise is only that it returns a Double.
        Formula.evaluate(formula, i.toDouble, 32.0, 1.0)
      }
    }
  }
