package it.unibo.demo.scenarios

/**
 * Covers the slot geometry, which is the part of a formation that can be checked without
 * standing up an aggregate round: every shape's `slots` delegates to one of these pure
 * functions.
 */
class FormationGeometrySuite extends munit.FunSuite:

  private val tolerance = 1e-9

  private def radiusOf(slot: (Double, Double)): Double =
    math.sqrt(slot._1 * slot._1 + slot._2 * slot._2)

  test("a ring places every slot at the requested radius, evenly spaced") {
    val slots = ShapeFormation.ring(6, 0.0)(_ => 0.5)
    assertEquals(slots.size, 6)
    slots.foreach(slot => assertEqualsDouble(radiusOf(slot), 0.5, tolerance))
    // Opposite slots of an even ring must be antipodal.
    val (first, opposite) = (slots.head, slots(3))
    assertEqualsDouble(first._1 + opposite._1, 0.0, 1e-9)
    assertEqualsDouble(first._2 + opposite._2, 0.0, 1e-9)
  }

  test("a ring is empty when there is nobody to place") {
    assertEquals(ShapeFormation.ring(0, 0.0)(_ => 1.0), List.empty)
    assertEquals(ShapeFormation.ring(-1, 0.0)(_ => 1.0), List.empty)
  }

  test("a bearing offset rotates the whole ring without changing its radii") {
    val quarterTurn = math.Pi / 2
    val rotated = ShapeFormation.ring(4, quarterTurn)(_ => 1.0)
    val plain = ShapeFormation.ring(4, 0.0)(_ => 1.0)
    // A quarter turn on a 4-slot ring maps each slot onto the next one.
    assertEqualsDouble(rotated.head._1, plain(1)._1, 1e-9)
    assertEqualsDouble(rotated.head._2, plain(1)._2, 1e-9)
    rotated.foreach(slot => assertEqualsDouble(radiusOf(slot), 1.0, tolerance))
  }

  test("the shared phase completes one turn per period") {
    assertEqualsDouble(ShapeFormation.phaseAt(0L, 6.0), 0.0, tolerance)
    assertEqualsDouble(ShapeFormation.phaseAt(3_000L, 6.0), math.Pi, tolerance)
    assertEqualsDouble(ShapeFormation.phaseAt(6_000L, 6.0), 2 * math.Pi, tolerance)
  }

  test("a non-positive period freezes the phase instead of dividing by zero") {
    assertEqualsDouble(ShapeFormation.phaseAt(1_234L, 0.0), 0.0, tolerance)
    assertEqualsDouble(ShapeFormation.phaseAt(1_234L, -1.0), 0.0, tolerance)
  }

  test("a breathing ring reaches its widest a quarter of the way through the period") {
    val rest = 0.6
    val amplitude = 0.2
    def radiusAt(phase: Double): Double =
      math.max(ShapeFormation.MinRadius, rest + amplitude * math.sin(phase))
    assertEqualsDouble(radiusAt(ShapeFormation.phaseAt(0L, 4.0)), rest, tolerance)
    assertEqualsDouble(radiusAt(ShapeFormation.phaseAt(1_000L, 4.0)), rest + amplitude, tolerance)
    assertEqualsDouble(radiusAt(ShapeFormation.phaseAt(3_000L, 4.0)), rest - amplitude, tolerance)
  }

  test("a travelling wave puts neighbouring slots at different radii") {
    // One crest around the ring: the slot at the crest and the one opposite it must differ.
    val radii = ShapeFormation
      .ring(8, 0.0) { i =>
        val spatial = 2 * math.Pi * 1.0 * i / 8
        math.max(ShapeFormation.MinRadius, 0.6 + 0.2 * math.sin(0.0 - spatial))
      }
      .map(radiusOf)
    assertEquals(radii.distinct.size > 1, true, s"expected a spread of radii, got $radii")
    assertEqualsDouble(radii(2), 0.6 - 0.2, 1e-9) // sin(-pi/2) = -1
    assertEqualsDouble(radii(6), 0.6 + 0.2, 1e-9) // sin(-3pi/2) = +1
  }

  test("a pulsing ring never collapses through its own centre") {
    // Amplitude deliberately larger than the rest radius.
    val radii = ShapeFormation
      .ring(4, 0.0)(_ => math.max(ShapeFormation.MinRadius, 0.1 + 1.0 * math.sin(-math.Pi / 2)))
      .map(radiusOf)
    radii.foreach(r => assertEqualsDouble(r, ShapeFormation.MinRadius, tolerance))
  }

  test("a grid leaves its centre cell to the anchor robot") {
    val slots = SquareFormation.grid(3, 0.4)
    assertEquals(slots.size, 3)
    assertEquals(slots.contains((0.0, 0.0)), false)
  }

  test("a grid always produces exactly as many slots as there are robots") {
    (1 to 12).foreach(count => assertEquals(SquareFormation.grid(count, 0.4).size, count))
  }

  test("the heart curve leaves its cusp to the anchor robot") {
    val slots = HeartFormation.curve(5, 0.06)
    assertEquals(slots.size, 5)
    // The cusp sits at the origin by construction, so no slot may land on it.
    slots.foreach(slot => assert(radiusOf(slot) > 1e-6, s"slot $slot landed on the cusp"))
  }

  test("a line spreads its slots either side of the anchor, leaving the origin free") {
    val offsets = ShapeFormation.lineOffsets(5, 0.4)
    assertEquals(offsets.size, 5)
    // Ordered end to end, so the crest of a wave over them travels monotonically.
    assertEquals(offsets, offsets.sorted)
    assertEquals(offsets.count(_ == 0.0), 0)
    // Two slots behind the anchor, three in front, at whole multiples of the spacing.
    offsets.zip(List(-0.8, -0.4, 0.4, 0.8, 1.2)).foreach { (actual, expected) =>
      assertEqualsDouble(actual, expected, 1e-9)
    }
  }

  test("a line is empty when there is nobody to place") {
    assertEquals(ShapeFormation.lineOffsets(0, 0.4), List.empty)
    assertEquals(ShapeFormation.lineOffsets(-3, 0.4), List.empty)
  }

  test("a rotation turns a slot set without changing its distances from the origin") {
    val quarterTurn = ShapeFormation.rotated(List((1.0, 0.0), (0.0, 2.0)), math.Pi / 2)
    assertEqualsDouble(quarterTurn.head._1, 0.0, 1e-9)
    assertEqualsDouble(quarterTurn.head._2, 1.0, 1e-9)
    assertEqualsDouble(quarterTurn(1)._1, -2.0, 1e-9)
    assertEqualsDouble(quarterTurn(1)._2, 0.0, 1e-9)
  }

  test("a sine line keeps its spacing along the axis and waves across it") {
    val slots = SineLineFormation.wave(8, 0.4, 0.3, 1.0, 0.0)
    assertEquals(slots.size, 8)
    // The x coordinates are exactly the line's, untouched by the wave.
    assertEquals(slots.map(_._1), ShapeFormation.lineOffsets(8, 0.4))
    // One crest across the line, so the displacements span the full amplitude.
    assert(slots.map(_._2).max > 0.2, s"the wave never crests: ${slots.map(_._2)}")
    assert(slots.map(_._2).min < -0.2, s"the wave never troughs: ${slots.map(_._2)}")
  }

  test("a sine line moves its crest along the line as the cycle advances") {
    def crestAt(phase: Double): Int =
      val displacements = SineLineFormation.wave(8, 0.4, 0.3, 1.0, phase).map(_._2)
      displacements.indexOf(displacements.max)
    assertNotEquals(crestAt(0.0), crestAt(math.Pi / 2), "the crest stayed put")
    // Half a cycle later the crest sits where the trough was.
    val start = SineLineFormation.wave(8, 0.4, 0.3, 1.0, 0.0).map(_._2)
    val halfway = SineLineFormation.wave(8, 0.4, 0.3, 1.0, math.Pi).map(_._2)
    start.zip(halfway).foreach((a, b) => assertEqualsDouble(a + b, 0.0, 1e-9))
  }

  test("a zero amplitude leaves the sine line straight") {
    SineLineFormation.wave(6, 0.4, 0.0, 1.0, 1.23).foreach { slot =>
      assertEqualsDouble(slot._2, 0.0, 1e-12)
    }
  }

  test("every shape returns nothing when the fleet is empty") {
    assertEquals(SquareFormation.grid(0, 0.4), List.empty)
    assertEquals(HeartFormation.curve(0, 0.06), List.empty)
    assertEquals(SineLineFormation.wave(0, 0.4, 0.3, 1.0, 0.0), List.empty)
  }
