package it.unibo.demo.scenarios

/**
 * Covers the safety envelope every designed layout passes through, which like every other shape
 * reduces to pure functions and needs no aggregate round to check -- the same split
 * [[FormationGeometrySuite]] relies on.
 *
 * The load-bearing property is the slot count. `AssignmentSolver.solveIndices` returns an empty
 * map on any mismatch between the number of robots and the number of targets, so a layout of the
 * wrong length must be refused here rather than handed on, where it would freeze the entire fleet
 * without a single log line anywhere.
 */
class CustomSlotsSuite extends munit.FunSuite:

  private val tolerance = 1e-9
  private val collisionArea = 0.3
  private val maxRadius = 1.5

  private def radiusOf(slot: (Double, Double)): Double = math.hypot(slot._1, slot._2)

  /** What the DSL's `outline` hands the envelope. */
  private def outline(points: List[(Double, Double)], closed: Boolean, count: Int): List[(Double, Double)] =
    CustomSlots.resample(CustomSlots.dedupeConsecutive(points), closed, count)

  private def slots(
      raw: List[(Double, Double)],
      scale: Double = 1.0,
      cap: Double = maxRadius,
      collision: Double = collisionArea
  ): List[(Double, Double)] =
    CustomSlots
      .slotsFor(raw, raw.size, scale, cap, collision)
      .getOrElse(fail(s"$raw should have produced slots"))

  private def gap = CustomSlots.minSeparation(collisionArea)

  private val square = List((0.5, 0.5), (0.5, -0.5), (-0.5, -0.5), (-0.5, 0.5))

  test("an empty fleet gets no slots, whatever the layout says") {
    assertEquals(CustomSlots.slotsFor(square, 0, 1.0, maxRadius, collisionArea), Some(List.empty))
    assertEquals(CustomSlots.slotsFor(square, -1, 1.0, maxRadius, collisionArea), Some(List.empty))
  }

  test("a layout of the wrong size asks the caller for a fallback instead of freezing the fleet") {
    (1 to 6).filter(_ != square.size).foreach { count =>
      assertEquals(CustomSlots.slotsFor(square, count, 1.0, maxRadius, collisionArea), None, s"fleet of $count")
    }
  }

  test("an outline always resamples to exactly as many slots as there are robots") {
    // The invariant the whole design hangs on; see the class docstring.
    val paths = (1 to 6).map(size => square.take(size).toList ++ List.fill(math.max(0, size - 4))((0.2, 0.2)))
    for
      count <- 1 to 14
      path <- paths
      closed <- List(false, true)
    do
      assertEquals(slots(outline(path, closed, count)).size, count, s"path of ${path.size}, closed=$closed, fleet of $count")
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
    val placed = slots(outline(List((0.6, 0.2)), closed = false, count = 4))
    assertEquals(placed.size, 4)
    assertEquals(placed.distinct.size, 4)
    placed.foreach { slot =>
      assertEqualsDouble(math.hypot(slot._1 - 0.6, slot._2 - 0.2), gap / 2, 1e-9)
    }
  }

  test("two identical points are treated as one") {
    val doubled = slots(outline(List((0.3, 0.3), (0.3, 0.3)), closed = false, count = 3))
    val single = slots(outline(List((0.3, 0.3)), closed = false, count = 3))
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

  test("a roomy outline keeps its size and stays on the outline as written") {
    // A triangle of side 1.44 m on eleven robots: the corners take a slot each, the spare ones
    // spread along the sides, and nothing about the corners reads as crowding.
    val triangle = List((0.0, 0.8), (0.8, -0.4), (-0.8, -0.4))
    val placed = slots(outline(triangle, closed = true, count = 11))
    val vertices = triangle.toIndexedSeq
    def toSegment(p: (Double, Double), a: (Double, Double), b: (Double, Double)): Double =
      val (dx, dy) = (b._1 - a._1, b._2 - a._2)
      val t = math.max(0.0, math.min(1.0, ((p._1 - a._1) * dx + (p._2 - a._2) * dy) / (dx * dx + dy * dy)))
      math.hypot(p._1 - (a._1 + t * dx), p._2 - (a._2 + t * dy))
    placed.foreach { slot =>
      val offOutline = (0 until 3).map(i => toSegment(slot, vertices(i), vertices((i + 1) % 3))).min
      assert(offOutline < 1e-6, s"$slot left the requested outline by $offOutline")
    }
    // The base corners, its farthest points, are where the author put them.
    assertEqualsDouble(placed.map(radiusOf).max, math.hypot(0.8, 0.4), 1e-6)
  }

  test("a slot on every corner leaves no pinch either side of it") {
    // Equal arc steps used to straddle each vertex with two slots inside the collision radius.
    val triangle = List((0.0, 0.8), (0.8, -0.4), (-0.8, -0.4))
    val placed = slots(outline(triangle, closed = true, count = 11))
    triangle.foreach { corner =>
      assert(placed.exists(slot => math.hypot(slot._1 - corner._1, slot._2 - corner._2) < 1e-9), s"$corner has no slot")
    }
    val chords = (placed :+ placed.head).sliding(2).collect { case List(a, b) => math.hypot(b._1 - a._1, b._2 - a._2) }.toList
    assert(chords.min >= gap, s"tightest chord was ${chords.min}")
  }

  test("a square's spare slots go to the middle of its sides") {
    val placed = CustomSlots.resample(square, closed = true, count = 8)
    val midpoints = List((0.5, 0.0), (0.0, -0.5), (-0.5, 0.0), (0.0, 0.5))
    (square ++ midpoints).foreach { expected =>
      assert(placed.exists(slot => math.hypot(slot._1 - expected._1, slot._2 - expected._2) < 1e-9), s"$expected has no slot")
    }
  }

  test("an open letter keeps every stroke end and bend") {
    // An M on nine robots: eight slots, five of them its vertices, the rest on the longest strokes.
    val letter = List((-0.6, -0.6), (-0.6, 0.6), (0.0, 0.0), (0.6, 0.6), (0.6, -0.6))
    val placed = CustomSlots.resample(letter, closed = false, count = 8)
    assertEquals(placed.size, 8)
    letter.foreach { vertex =>
      assert(placed.exists(slot => math.hypot(slot._1 - vertex._1, slot._2 - vertex._2) < 1e-9), s"$vertex has no slot")
    }
  }

  test("more corners than slots falls back to equal steps") {
    val star = (0 until 10).map { k =>
      val angle = 2 * math.Pi * k / 10
      val radius = if k % 2 == 0 then 0.8 else 0.35
      (math.sin(angle) * radius, math.cos(angle) * radius)
    }.toList
    assertEquals(CustomSlots.cornerIndices(star.toIndexedSeq, closed = true).size, 10)
    val placed = CustomSlots.resample(star, closed = true, count = 8)
    assertEquals(placed.size, 8)
    // Ten equal edges in eight equal steps: slot 0 on vertex 0, slot 4 half way round on vertex 5,
    // and every other slot part way along an edge rather than on a corner.
    List(0 -> 0, 4 -> 5).foreach { (slot, vertex) =>
      assertEqualsDouble(placed(slot)._1, star(vertex)._1, 1e-9)
      assertEqualsDouble(placed(slot)._2, star(vertex)._2, 1e-9)
    }
    assertEquals(placed.count(slot => star.exists(v => math.hypot(v._1 - slot._1, v._2 - slot._2) < 1e-9)), 2)
  }

  test("a sampled circle has no corners") {
    val circle = (0 until 16).map { k =>
      val angle = 2 * math.Pi * k / 16
      (math.sin(angle) * 0.5, math.cos(angle) * 0.5)
    }
    assertEquals(CustomSlots.cornerIndices(circle, closed = true), IndexedSeq.empty)
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
    // a uniform shrink would collapse the entire formation to a dot to accommodate exp(7).
    // Offset clear of the anchor keep-out radius, so this test is about the cap and nothing else.
    val placed = slots((0 until 8).map(i => (0.2 + math.exp(i) / 10, 0.0)).toList)
    assertEqualsDouble(placed.head._1, 0.2 + math.exp(0) / 10, 1e-9)
    assertEqualsDouble(placed(1)._1, 0.2 + math.exp(1) / 10, 1e-9)
    // The far ones all land on the cap, which makes them coincident, so the separation fan then
    // spreads them around that point: near the cap, and distinct rather than stacked.
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
    val placed = slots((0 until 8).map(i => (100.0 * i + 1, 100.0 * i + 1)).toList, cap = 1e6)
    val ceiling = CustomSlots.AbsoluteMaxRadius + gap / 2 + 1e-9
    placed.foreach(slot => assert(radiusOf(slot) <= ceiling, s"$slot passed the ceiling"))
  }

  test("a layout that blows up still gives usable slots") {
    val placed = slots((0 until 6).map(i => (1.0 / i, math.log(-1.0))).toList)
    assertEquals(placed.size, 6)
    placed.foreach { slot =>
      assert(slot._1.isFinite && slot._2.isFinite, s"$slot is not finite")
      assert(radiusOf(slot) <= CustomSlots.AbsoluteMaxRadius + gap, s"$slot escaped the cap")
    }
  }

  test("the operator scale resizes a layout without reshaping it") {
    // Roomy enough that growToFit stays out of it at both scales; the interaction between the two
    // has its own test below.
    val layout = outline(List((0.4, 0.0), (0.0, 0.4), (-0.4, 0.0)), closed = false, count = 3)
    val plain = slots(layout)
    val doubled = slots(layout, scale = 2.0)
    plain.zip(doubled).foreach { case (before, after) =>
      assertEqualsDouble(radiusOf(after), radiusOf(before) * 2, 1e-9)
      assertEqualsDouble(math.atan2(after._1, after._2), math.atan2(before._1, before._2), 1e-9)
    }
  }

  test("scaling up a layout the fleet had to be grown into stops growing it") {
    // growToFit only rescues a shape too small to stand on. Slots 0.2 apart, under the 0.3
    // minimum, are grown; at scale 2 they already clear it and stay exactly as written. Doubling
    // the scale therefore does not quite double the radii. Off to the side, so the anchor
    // keep-out plays no part.
    val cramped = List((0.4, -0.2), (0.4, 0.0), (0.4, 0.2))
    val plain = slots(cramped)
    val doubled = slots(cramped, scale = 2.0)
    val spacingOf = (placed: List[(Double, Double)]) =>
      placed.sliding(2).collect { case List(a, b) => math.hypot(b._1 - a._1, b._2 - a._2) }.min
    assertEqualsDouble(spacingOf(plain), gap, 1e-9)
    assertEqualsDouble(radiusOf(doubled(1)), 0.8, 1e-9)
    // Still monotone in the scale, which is what an operator dragging the slider expects.
    assert(radiusOf(doubled(1)) > radiusOf(plain(1)))
  }

  test("an absurd or non-finite scale is clamped rather than obeyed") {
    List(1e9, 0.0, -3.0, Double.NaN, Double.PositiveInfinity).foreach { scale =>
      val placed = slots(square, scale = scale)
      assertEquals(placed.size, 4, s"scale $scale")
      placed.foreach { slot =>
        assert(slot._1.isFinite && slot._2.isFinite, s"scale $scale gave $slot")
        assert(radiusOf(slot) <= maxRadius + gap, s"scale $scale gave $slot")
      }
    }
  }

  test("the whole envelope is total") {
    val hostile: List[Int => List[(Double, Double)]] = List(
      count => List.fill(count)((0.0, 0.0)),
      count => List.fill(count)((0.1, 0.1)),
      count => (0 until count).map(i => if i % 2 == 0 then (1e9, -1e9) else (0.0, 0.0)).toList,
      count => (0 until count).map(i => (1.0 / i, math.log(-1.0))).toList,
      count => (0 until count).map(i => (math.exp(math.exp(i)), math.pow(0, 0))).toList,
      count => (0 until count).map(i => (math.sqrt(-i), math.tan(math.Pi / 2))).toList
    )
    for
      layout <- hostile
      count <- 0 to 16
      scale <- List(1.0, Double.NaN, 1e9)
      collision <- List(0.3, 0.0, Double.NaN)
      cap <- List(maxRadius, 0.0, Double.NaN)
    do
      val placed = CustomSlots.slotsFor(layout(count), count, scale, cap, collision)
        .getOrElse(fail(s"a layout of the right size produced no slots at $count"))
      assertEquals(placed.size, count)
      placed.foreach { slot =>
        assert(slot._1.isFinite && slot._2.isFinite, s"count=$count scale=$scale cap=$cap gave $slot")
      }
  }

  test("a ring too tight for the fleet is grown to the clearance") {
    val placed = slots(ShapeFormation.ring(8, 0.0)(_ => 0.2))
    val closest = placed.combinations(2).map { case List(a, b) => math.hypot(a._1 - b._1, a._2 - b._2) }.min
    assertEqualsDouble(closest, gap, 1e-9)
  }

  test("the travel of a spinning layout is its radius per radian, and a still one does not move") {
    val layout = (spins: Boolean) => (phase: Double) =>
      CustomSlots.slotsFor(ShapeFormation.ring(6, if spins then phase else 0.0)(_ => 0.6), 6, 1.0, maxRadius, collisionArea)
    val step = 2 * math.Pi / CustomSlots.TravelSamples
    // A chord, not the arc: a hair under the radius.
    assertEqualsDouble(CustomSlots.travel(layout(true)), 0.6 * 2 * math.sin(step / 2) / step, 1e-9)
    assertEqualsDouble(CustomSlots.travel(layout(false)), 0.0, 1e-9)
  }
