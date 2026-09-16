package it.unibo.demo.scenarios

/**
 * Covers the geometry of a data-driven formation, which like every other shape reduces to a pure
 * function and needs no aggregate round to check -- the same split
 * [[FormationGeometrySuite]] relies on.
 *
 * The load-bearing property is the slot count. `AssignmentSolver.solveIndices` returns an empty
 * map on any mismatch between the number of robots and the number of targets, so a `slots` that
 * returns the wrong length freezes the entire fleet without a single log line anywhere.
 */
class CustomSlotsSuite extends munit.FunSuite:

  private val tolerance = 1e-9
  private val collisionArea = 0.3
  private val maxRadius = 1.5

  private def radiusOf(slot: (Double, Double)): Double = math.hypot(slot._1, slot._2)

  private def ctx(count: Int, phase: Double = 0.0) = SlotContext(count, phase, 0.0)

  private def formula(source: String): Formula =
    Formula.compile(source).getOrElse(fail(s"'$source' should parse"))

  private def cartesianSpec(x: String, y: String) = CustomSpec.Cartesian(formula(x), formula(y))
  private def polarSpec(r: String, theta: String) = CustomSpec.Polar(formula(r), formula(theta))

  private def slots(
      spec: CustomSpec,
      count: Int,
      scale: Double = 1.0,
      cap: Double = maxRadius,
      phase: Double = 0.0,
      collision: Double = 0.3
  ): List[(Double, Double)] =
    CustomSlots
      .slotsFor(spec, ctx(count, phase), scale, cap, collision)
      .getOrElse(fail(s"$spec should have produced slots"))

  private def gap = CustomSlots.minSeparation(collisionArea)

  private val square = List((0.5, 0.5), (0.5, -0.5), (-0.5, -0.5), (-0.5, 0.5))

  test("an empty fleet gets no slots") {
    val specs = List(
      CustomSpec.Points(square, closed = true),
      cartesianSpec("i", "0"),
      polarSpec("0.5", "i")
    )
    specs.foreach { spec =>
      assertEquals(slots(spec, 0), List.empty)
      assertEquals(slots(spec, -1), List.empty)
    }
  }

  test("a spec always produces exactly as many slots as there are robots") {
    // The invariant the whole design hangs on; see the class docstring.
    val paths = (1 to 6).map(size => square.take(size).toList ++ List.fill(math.max(0, size - 4))((0.2, 0.2)))
    for
      count <- 1 to 14
      path <- paths
      closed <- List(false, true)
    do
      assertEquals(
        slots(CustomSpec.Points(path, closed), count).size,
        count,
        s"points path of ${path.size}, closed=$closed, fleet of $count"
      )
    for count <- 1 to 14 do
      assertEquals(slots(cartesianSpec("0.2*i", "0.1*n"), count).size, count)
      assertEquals(slots(polarSpec("0.5", "2*pi*i/n"), count).size, count)
  }

  test("an open path keeps a slot on each of its ends") {
    val path = List((-1.0, 0.0), (0.0, 1.0), (1.0, 0.0))
    val sampled = CustomSlots.resample(path, closed = false, count = 5)
    assertEquals(sampled.size, 5)
    assertEqualsDouble(sampled.head._1, -1.0, tolerance)
    assertEqualsDouble(sampled.head._2, 0.0, tolerance)
    assertEqualsDouble(sampled.last._1, 1.0, tolerance)
    assertEqualsDouble(sampled.last._2, 0.0, tolerance)
  }

  test("an open path spaces its slots at equal arc length") {
    val straight = List((0.0, 0.0), (1.0, 0.0))
    val sampled = CustomSlots.resample(straight, closed = false, count = 5)
    val gaps = sampled.sliding(2).collect { case List(a, b) => math.hypot(b._1 - a._1, b._2 - a._2) }.toList
    gaps.foreach(step => assertEqualsDouble(step, 0.25, tolerance))
  }

  test("an evenly spaced path resampled to its own length comes back unchanged") {
    // Least surprise for an author who wrote exactly one point per robot.
    val even = (0 until 5).map(k => (k * 0.25, 0.0)).toList
    val sampled = CustomSlots.resample(even, closed = false, count = 5)
    sampled.zip(even).foreach { case (got, expected) =>
      assertEqualsDouble(got._1, expected._1, 1e-9)
      assertEqualsDouble(got._2, expected._2, 1e-9)
    }
  }

  test("a closed path leaves no duplicate slot at the seam") {
    val corners = CustomSlots.resample(square, closed = true, count = 4)
    assertEquals(corners.size, 4)
    assertEquals(corners.distinct.size, 4)
    val eight = CustomSlots.resample(square, closed = true, count = 8)
    assertEquals(eight.distinct.size, 8)
    // The last slot must not have wrapped back onto the first.
    assert(math.hypot(eight.head._1 - eight.last._1, eight.head._2 - eight.last._2) > 0.1)
  }

  test("a closed path resampled to the fleet size reproduces a ring") {
    val circle = (0 until 16).map { k =>
      val angle = 2 * math.Pi * k / 16
      (math.sin(angle) * 0.5, math.cos(angle) * 0.5)
    }.toList
    val sampled = CustomSlots.resample(circle, closed = true, count = 8)
    assertEquals(sampled.size, 8)
    // Chords cut the corners slightly, so the radius comes back a shade under 0.5.
    sampled.foreach(slot => assertEqualsDouble(radiusOf(slot), 0.5, 5e-3))
    val bearings = sampled.map(slot => math.atan2(slot._1, slot._2))
    val steps = bearings.sliding(2).collect { case List(a, b) =>
      val delta = b - a
      if delta < 0 then delta + 2 * math.Pi else delta
    }.toList
    steps.foreach(step => assertEqualsDouble(step, 2 * math.Pi / 8, 1e-6))
  }

  test("a single point fans the fleet out around it instead of stacking them") {
    val placed = slots(CustomSpec.Points(List((0.6, 0.2)), closed = false), 4)
    assertEquals(placed.size, 4)
    assertEquals(placed.distinct.size, 4)
    placed.foreach { slot =>
      assertEqualsDouble(math.hypot(slot._1 - 0.6, slot._2 - 0.2), gap / 2, 1e-9)
    }
  }

  test("two identical points are treated as one") {
    val doubled = slots(CustomSpec.Points(List((0.3, 0.3), (0.3, 0.3)), closed = false), 3)
    val single = slots(CustomSpec.Points(List((0.3, 0.3)), closed = false), 3)
    assertEquals(doubled, single)
  }

  test("a path shorter than the fleet can stand on is grown, not squashed") {
    val tight = (1 to 4).map(k => (k * 0.05, 0.0)).toList
    val grown = CustomSlots.growToFit(tight, spacing = 0.05, minSpacing = 0.3, maxRadius = 10.0)
    val gaps = grown.sliding(2).collect { case List(a, b) => math.hypot(b._1 - a._1, b._2 - a._2) }.toList
    gaps.foreach(step => assertEqualsDouble(step, 0.3, 1e-9))
    // Similar, not distorted: every slot is the same positive multiple of its original vector.
    val factors = grown.zip(tight).map { case (after, before) => after._1 / before._1 }
    factors.foreach(factor => assertEqualsDouble(factor, factors.head, 1e-9))
    assert(factors.head > 1.0)
  }

  test("a path already long enough is left alone") {
    val roomy = (1 to 4).map(k => (k * 0.5, 0.0)).toList
    assertEquals(CustomSlots.growToFit(roomy, spacing = 0.5, minSpacing = 0.3, maxRadius = 10.0), roomy)
    // Never shrinks, even when the slots already sit outside the cap.
    assertEquals(CustomSlots.growToFit(roomy, spacing = 0.5, minSpacing = 0.3, maxRadius = 0.1), roomy)
  }

  test("growth stops at the radius cap") {
    val tight = (1 to 4).map(k => (k * 0.05, 0.0)).toList
    val grown = CustomSlots.growToFit(tight, spacing = 0.05, minSpacing = 0.3, maxRadius = 0.4)
    grown.foreach(slot => assert(radiusOf(slot) <= 0.4 + 1e-9, s"$slot breached the cap"))
  }

  test("a corner does not make a roomy path look too small for the fleet") {
    // Found end to end: a triangle of side 1.44 m sampled every 0.41 m has two slots only 0.19 m
    // apart in a straight line either side of each vertex. Judging crowding by that shortest
    // chord read the corner as "too small" and inflated the whole triangle by half again, so the
    // fleet settled dead on a shape 1.54x the one that was published.
    val triangle = List((0.0, 0.8), (0.8, -0.4), (-0.8, -0.4))
    val spacing = CustomSlots.pathSpacing(triangle, closed = true, count = 11)
    assertEqualsDouble(spacing, 4.4844 / 11, 1e-3)
    assert(spacing > 0.3, s"the probe path is genuinely crowded at $spacing")

    val placed = slots(CustomSpec.Points(triangle, closed = true), 11)
    // Every slot stays on the triangle as written, corners and all.
    val vertices = triangle.toIndexedSeq
    def toSegment(p: (Double, Double), a: (Double, Double), b: (Double, Double)): Double =
      val (dx, dy) = (b._1 - a._1, b._2 - a._2)
      val t = math.max(0.0, math.min(1.0, ((p._1 - a._1) * dx + (p._2 - a._2) * dy) / (dx * dx + dy * dy)))
      math.hypot(p._1 - (a._1 + t * dx), p._2 - (a._2 + t * dy))
    placed.foreach { slot =>
      val offOutline = (0 until 3).map(i => toSegment(slot, vertices(i), vertices((i + 1) % 3))).min
      assert(offOutline < 1e-6, s"$slot left the requested outline by $offOutline")
    }
    // And the shape keeps its size: the apex is where the author put it.
    assertEqualsDouble(placed.map(radiusOf).max, 0.8, 1e-6)
  }

  test("a chord shorter than the minimum at a corner is left to the repulsion") {
    // The pinch is real -- two slots either side of a vertex are inside the collision radius --
    // and deliberately not corrected here: growing the shape to fix one corner distorts nothing
    // but does move every other robot.
    val triangle = List((0.0, 0.8), (0.8, -0.4), (-0.8, -0.4))
    val placed = slots(CustomSpec.Points(triangle, closed = true), 11)
    val chords = placed.sliding(2).collect { case List(a, b) => math.hypot(b._1 - a._1, b._2 - a._2) }.toList
    assert(chords.min < gap, s"expected a corner pinch, tightest chord was ${chords.min}")
    assertEqualsDouble(chords.max, 4.4844 / 11, 1e-3)
  }

  test("a non-finite coordinate is replaced rather than propagated") {
    val nasty = List(
      (Double.NaN, 1.0),
      (1.0, Double.PositiveInfinity),
      (Double.NegativeInfinity, Double.NaN)
    )
    CustomSlots.finite(nasty).foreach { slot =>
      assert(slot._1.isFinite && slot._2.isFinite, s"$slot is not finite")
    }
  }

  test("a slot beyond the radius cap is pulled back along its own bearing") {
    val far = List((10.0, 0.0), (0.0, -8.0), (6.0, 6.0))
    val clamped = CustomSlots.clampRadius(far, 1.5)
    clamped.foreach(slot => assertEqualsDouble(radiusOf(slot), 1.5, 1e-9))
    clamped.zip(far).foreach { case (after, before) =>
      assertEqualsDouble(math.atan2(after._1, after._2), math.atan2(before._1, before._2), 1e-9)
    }
  }

  test("one runaway slot does not shrink the rest of the formation") {
    // This is why the cap is applied per slot rather than as a uniform shrink of the whole set:
    // a uniform shrink would collapse the entire formation to a dot to accommodate exp(15).
    // Offset clear of the anchor keep-out radius, so this test is about the cap and nothing else.
    val placed = slots(cartesianSpec("0.2+exp(i)/10", "0"), 8)
    // The near slots keep the value the formula asked for.
    assertEqualsDouble(placed.head._1, 0.2 + math.exp(0) / 10, 1e-9)
    assertEqualsDouble(placed(1)._1, 0.2 + math.exp(1) / 10, 1e-9)
    // The far ones all land on the cap, which makes them coincident, so the separation fan then
    // spreads them around that point -- the documented consequence that a slot may end up within
    // half a minimum separation either side of the cap. What must hold is that they stay near it
    // and stay distinct, rather than stacking into one spot.
    val far = placed.drop(4)
    far.foreach { slot =>
      assert(radiusOf(slot) <= maxRadius + gap / 2 + 1e-9, s"$slot passed the cap")
      assert(radiusOf(slot) >= maxRadius - gap / 2 - 1e-9, s"$slot fell short of the cap")
    }
    assertEquals(far.distinct.size, far.size)
  }

  test("no slot is ever left standing on the anchor") {
    val stacked = List.fill(5)((0.0, 0.0))
    val separated = CustomSlots.nudgeOffAnchor(CustomSlots.separateCoincident(stacked, gap), gap / 2)
    assertEquals(separated.size, 5)
    assertEquals(separated.distinct.size, 5)
    separated.foreach(slot => assertEqualsDouble(radiusOf(slot), gap / 2, 1e-9))
    // Evenly spread in bearing, so the result is deterministic rather than arbitrary.
    val bearings = separated.map(slot => math.atan2(slot._1, slot._2)).sorted
    bearings.sliding(2).collect { case List(a, b) => b - a }
      .foreach(step => assertEqualsDouble(step, 2 * math.Pi / 5, 1e-6))
  }

  test("the absolute radius ceiling cannot be raised by a payload") {
    val placed = slots(cartesianSpec("100*i+1", "100*i+1"), 8, cap = 1e6)
    val ceiling = CustomSlots.AbsoluteMaxRadius + gap / 2 + 1e-9
    placed.foreach(slot => assert(radiusOf(slot) <= ceiling, s"$slot passed the ceiling"))
  }

  test("cartesian formulas are evaluated once per slot with i, n and t in scope") {
    val raw = CustomSlots.cartesian(formula("i"), formula("n"), ctx(4))
    assertEquals(raw, List((0.0, 4.0), (1.0, 4.0), (2.0, 4.0), (3.0, 4.0)))
  }

  test("polar formulas use the same bearing convention as every other shape") {
    // A constant radius on an evenly divided angle must be indistinguishable from the built-in
    // ring, which pins the convention for every formula an author will ever write.
    val designed = CustomSlots.polar(formula("0.5"), formula("2*pi*i/n"), ctx(6))
    val builtin = ShapeFormation.ring(6, 0.0)(_ => 0.5)
    designed.zip(builtin).foreach { case (got, expected) =>
      assertEqualsDouble(got._1, expected._1, 1e-9)
      assertEqualsDouble(got._2, expected._2, 1e-9)
    }
  }

  test("the phase reaches a formula that asks for it") {
    val spec = polarSpec("0.5", "2*pi*i/n + t")
    val still = slots(spec, 6, phase = 0.0)
    val turned = slots(spec, 6, phase = math.Pi / 2)
    still.zip(turned).foreach { case (before, after) =>
      assert(math.hypot(after._1 - before._1, after._2 - before._2) > 1e-3, "the ring did not turn")
      assertEqualsDouble(radiusOf(after), radiusOf(before), 1e-9)
    }
  }

  test("a formula that blows up still gives a usable slot") {
    val placed = slots(cartesianSpec("1/i", "log(0-1)"), 6)
    assertEquals(placed.size, 6)
    placed.foreach { slot =>
      assert(slot._1.isFinite && slot._2.isFinite, s"$slot is not finite")
      assert(radiusOf(slot) <= CustomSlots.AbsoluteMaxRadius + gap, s"$slot escaped the cap")
    }
  }

  test("an absent or rejected spec asks the caller for a fallback") {
    List(CustomSpec.Absent, CustomSpec.Invalid("why")).foreach { spec =>
      (1 to 5).foreach { count =>
        assertEquals(CustomSlots.slotsFor(spec, ctx(count), 1.0, maxRadius, collisionArea), None)
      }
      // Even an empty fleet: there is nothing to lay out, so the count guard answers first.
      assertEquals(CustomSlots.slotsFor(spec, ctx(0), 1.0, maxRadius, collisionArea), Some(List.empty))
    }
  }

  test("the operator scale resizes a spec without reshaping it") {
    // Roomy enough that growToFit stays out of it at both scales; the interaction between the two
    // has its own test below.
    val spec = CustomSpec.Points(List((0.4, 0.0), (0.0, 0.4), (-0.4, 0.0)), closed = false)
    val plain = slots(spec, 3)
    val doubled = slots(spec, 3, scale = 2.0)
    plain.zip(doubled).foreach { case (before, after) =>
      assertEqualsDouble(radiusOf(after), radiusOf(before) * 2, 1e-9)
      assertEqualsDouble(math.atan2(after._1, after._2), math.atan2(before._1, before._2), 1e-9)
    }
  }

  test("scaling up a path the fleet had to be grown into stops growing it") {
    // growToFit only rescues a shape too small to stand on. A path at 0.2 has adjacent slots
    // 0.283 apart, under the 0.3 minimum, so it is grown; the same path at scale 2 already clears
    // the minimum and is left exactly as written. Doubling the scale therefore does not quite
    // double the radii -- worth knowing before reading it as a bug.
    val cramped = CustomSpec.Points(List((0.2, 0.0), (0.0, 0.2), (-0.2, 0.0)), closed = false)
    val plain = slots(cramped, 3)
    val doubled = slots(cramped, 3, scale = 2.0)
    val spacingOf = (placed: List[(Double, Double)]) =>
      placed.sliding(2).collect { case List(a, b) => math.hypot(b._1 - a._1, b._2 - a._2) }.min
    assertEqualsDouble(spacingOf(plain), gap, 1e-9)
    assertEqualsDouble(radiusOf(doubled.head), 0.4, 1e-9)
    // Still monotone in the scale, which is what an operator dragging the slider expects.
    assert(radiusOf(doubled.head) > radiusOf(plain.head))
  }

  test("an absurd or non-finite scale is clamped rather than obeyed") {
    val spec = CustomSpec.Points(square, closed = true)
    List(1e9, 0.0, -3.0, Double.NaN, Double.PositiveInfinity).foreach { scale =>
      val placed = slots(spec, 4, scale = scale)
      assertEquals(placed.size, 4, s"scale $scale")
      placed.foreach { slot =>
        assert(slot._1.isFinite && slot._2.isFinite, s"scale $scale gave $slot")
        assert(radiusOf(slot) <= maxRadius + gap, s"scale $scale gave $slot")
      }
    }
  }

  test("the whole pipeline is total") {
    val hostile = List(
      CustomSpec.Points(List((0.0, 0.0)), closed = false),
      CustomSpec.Points(List.fill(4)((0.0, 0.0)), closed = true),
      CustomSpec.Points(List.fill(CustomSpec.MaxPoints)((0.1, 0.1)), closed = false),
      CustomSpec.Points(List((1e9, -1e9), (0.0, 0.0)), closed = true),
      cartesianSpec("1/i", "log(0-1)"),
      cartesianSpec("exp(exp(i))", "0^0"),
      polarSpec("0-5", "tan(pi/2)"),
      polarSpec("sqrt(0-i)", "i%0")
    )
    for
      spec <- hostile
      count <- 0 to 16
      scale <- List(1.0, Double.NaN, 1e9)
      collision <- List(0.3, 0.0, Double.NaN)
      cap <- List(maxRadius, 0.0, Double.NaN)
    do
      val placed = CustomSlots.slotsFor(spec, ctx(count, 1.0), scale, cap, collision)
        .getOrElse(fail(s"$spec produced no slots"))
      assertEquals(placed.size, math.max(count, 0), s"$spec at $count")
      placed.foreach { slot =>
        assert(
          slot._1.isFinite && slot._2.isFinite,
          s"$spec at count=$count scale=$scale cap=$cap gave $slot"
        )
      }
  }
