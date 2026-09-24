package it.unibo.demo.scenarios

/** Dashboard's `custom` payload, compiled at the edge and applied atomically per round. */
enum CustomSpec:
  case Absent

  /** `reason` is what to show the author. */
  case Invalid(reason: String)

  /** Path in metres, anchor at the origin, resampled to the fleet size at equal arc length. */
  case Points(points: List[(Double, Double)], closed: Boolean)

  case Cartesian(x: Formula, y: Formula)

  /** `theta` follows [[ShapeFormation.ring]]'s bearing exactly, so a constant radius reproduces
    * [[CircleFormation]] bit for bit.
    */
  case Polar(r: Formula, theta: Formula)

object CustomSpec:
  /** Longest point list a payload may carry, before resampling. */
  val MaxPoints: Int = 256

/** Turns a [[CustomSpec]] into a slot set a fleet can execute. Pure and total throughout. */
object CustomSlots:

  /** No payload can exceed this, whatever it says. Enforced in [[slotsFor]], not by the caller. */
  val AbsoluteMaxRadius: Double = 3.0

  /** Two points closer than this are the same point. */
  val Epsilon: Double = 1e-6

  /** Bounds on the operator's live scale knob, so it cannot itself become the hazard. */
  val MinScale: Double = 0.05
  val MaxScale: Double = 10.0

  /** Samples per cycle when measuring how fast a time-varying spec moves its slots. */
  val TravelSamples: Int = 24

  /** Closest pair the controller holds without fighting itself: [[ShapeFormation.clearance]]. */
  def minSeparation(clearance: Double): Double =
    if !clearance.isFinite then ShapeFormation.MinRadius
    else math.max(ShapeFormation.MinRadius, clearance)

  /**
   * `None` (absent or rejected) leaves the fallback policy to [[CustomFormation]]. `Some` holds
   * exactly `ctx.count` finite entries - load-bearing, since `AssignmentSolver.solveIndices`
   * returns an empty map on a size mismatch and silently freezes the fleet.
   */
  def slotsFor(
      spec: CustomSpec,
      ctx: SlotContext,
      scale: Double,
      maxRadius: Double,
      clearance: Double
  ): Option[List[(Double, Double)]] =
    if ctx.count <= 0 then Some(List.empty)
    else
      spec match
        case CustomSpec.Absent | CustomSpec.Invalid(_) => None
        case _ =>
          val cap =
            if maxRadius.isFinite then math.min(math.max(maxRadius, ShapeFormation.MinRadius), AbsoluteMaxRadius)
            else AbsoluteMaxRadius
          val gap = minSeparation(clearance)
          val k =
            if scale.isFinite then math.min(MaxScale, math.max(MinScale, scale)) else 1.0
          // A path is judged by arc length; a formula has no uniform spacing, so by its tightest pair.
          val (raw, spacing) = spec match
            case CustomSpec.Points(points, closed) =>
              val path = dedupeConsecutive(points)
              (resample(path, closed, ctx.count), pathSpacing(path, closed, ctx.count))
            case CustomSpec.Cartesian(x, y) => withTightestPair(cartesian(x, y, ctx))
            case CustomSpec.Polar(r, theta) => withTightestPair(polar(r, theta, ctx))
            case _ => (List.empty, 0.0)
          // Coerce first: a runaway infinity becomes an origin-coincident slot instead of
          // dominating the growth factor.
          val scaled = finite(raw).map((x, y) => (x * k, y * k))
          val grown = growToFit(scaled, spacing * k, gap, cap)
          // The anchor is a robot too: a slot any nearer than `gap` is one its neighbour cannot hold.
          Some(nudgeOffAnchor(separateCoincident(clampRadius(grown, cap), gap), gap))

  /**
   * Metres a slot moves per radian of phase, at most, sampled over one cycle. The wrap back to
   * phase zero is left out: a spec that jumps there asks for a jump no speed limit can smooth.
   */
  def travel(layout: Double => Option[List[(Double, Double)]]): Double =
    val step = 2 * math.Pi / TravelSamples
    val frames = (0 until TravelSamples).map(k => layout(k * step))
    val moves = frames.sliding(2).collect {
      case Seq(Some(a), Some(b)) => a.zip(b).map((p, q) => distance(p, q)).maxOption.getOrElse(0.0)
    }
    moves.maxOption.getOrElse(0.0) / step

  /** Coincident slots are [[separateCoincident]]'s to fan out, so they do not count as the pair.
    * ponytail: quadratic, fine for a fleet of a few dozen.
    */
  private def withTightestPair(slots: List[(Double, Double)]): (List[(Double, Double)], Double) =
    val gaps = slots.combinations(2).collect { case List(a, b) if distance(a, b) >= Epsilon => distance(a, b) }
    (slots, gaps.minOption.getOrElse(0.0))

  /** Consecutive duplicates within [[Epsilon]] collapsed, and a closing duplicate dropped. */
  def dedupeConsecutive(points: List[(Double, Double)]): List[(Double, Double)] =
    val kept = points.foldLeft(List.empty[(Double, Double)]) { (acc, point) =>
      acc.headOption match
        case Some(previous) if distance(previous, point) < Epsilon => acc
        case _ => point :: acc
    }.reverse
    kept match
      case first :: rest if rest.nonEmpty && distance(first, kept.last) < Epsilon => kept.init
      case _ => kept

  /** Cumulative arc length along the polyline, walking the wrap segment when `closed`. */
  def arcLengths(points: IndexedSeq[(Double, Double)], closed: Boolean): IndexedSeq[Double] =
    val steps = if closed then points.size else points.size - 1
    val builder = IndexedSeq.newBuilder[Double]
    builder += 0.0
    var total = 0.0
    var index = 0
    while index < steps do
      total += distance(points(index), points((index + 1) % points.size))
      builder += total
      index += 1
    builder.result()

  /** `count` equally spaced points; degenerate paths yield copies for [[separateCoincident]]. */
  def resample(points: List[(Double, Double)], closed: Boolean, count: Int): List[(Double, Double)] =
    if count <= 0 then List.empty
    else if points.isEmpty then List.fill(count)((0.0, 0.0))
    else
      val vertices = points.toIndexedSeq
      if vertices.size == 1 then List.fill(count)(vertices.head)
      else
        val lengths = arcLengths(vertices, closed)
        val total = lengths.last
        if total < Epsilon then List.fill(count)(vertices.head)
        else
          (0 until count).map { step =>
            val target =
              if count == 1 then 0.0
              else if closed then total * step / count
              else total * step / (count - 1)
            pointAt(vertices, lengths, closed, target)
          }.toList

  /** Non-finite coordinates replaced by zero, per coordinate. */
  def finite(slots: List[(Double, Double)]): List[(Double, Double)] =
    slots.map((x, y) => (if x.isFinite then x else 0.0, if y.isFinite then y else 0.0))

  /**
   * Per slot, not a uniform shrink: a uniform one couples the slots, so a single runaway formula
   * value collapses the whole formation to a dot. Wrong-unit paths are [[growToFit]]'s job.
   */
  def clampRadius(slots: List[(Double, Double)], maxRadius: Double): List[(Double, Double)] =
    slots.map { (x, y) =>
      val radius = math.hypot(x, y)
      if radius <= maxRadius || radius < Epsilon then (x, y)
      else
        val factor = maxRadius / radius
        (x * factor, y * factor)
    }

  /** Arc length, uniform by construction - unlike the chord, which shortens at every corner. */
  def pathSpacing(points: List[(Double, Double)], closed: Boolean, count: Int): Double =
    if count <= 1 then 0.0
    else
      val vertices = points.toIndexedSeq
      if vertices.size < 2 then 0.0
      else
        val total = arcLengths(vertices, closed).last
        if closed then total / count else total / (count - 1)

  /** Grows undersized shapes to `minSpacing`, without exceeding `maxRadius` or distorting them. */
  def growToFit(
      slots: List[(Double, Double)],
      spacing: Double,
      minSpacing: Double,
      maxRadius: Double
  ): List[(Double, Double)] =
    if slots.size < 2 || spacing <= Epsilon || spacing >= minSpacing then slots
    else
      val wanted = minSpacing / spacing
      val furthest = slots.map((x, y) => math.hypot(x, y)).max
      val allowed = if furthest < Epsilon then wanted else maxRadius / furthest
      val factor = math.max(1.0, math.min(wanted, allowed))
      slots.map((x, y) => (x * factor, y * factor))

  /**
   * Coincident slots fanned onto a ring of `minSeparation / 2`, bearings by position so the result
   * is deterministic. After [[clampRadius]] on purpose: clamping itself stacks same-bearing slots.
   */
  def separateCoincident(
      slots: List[(Double, Double)],
      minSeparation: Double
  ): List[(Double, Double)] =
    // Quadratic sweep: a rounded grid would miss pairs straddling a cell boundary and leave those
    // robots stacked. Fine for a few dozen slots.
    val clusters = scala.collection.mutable.ListBuffer.empty[((Double, Double), scala.collection.mutable.ListBuffer[Int])]
    slots.zipWithIndex.foreach { case (slot, index) =>
      clusters.find((centre, _) => distance(centre, slot) < Epsilon) match
        case Some((_, members)) => members += index
        case None => clusters += ((slot, scala.collection.mutable.ListBuffer(index)))
    }
    val relocated = Array.ofDim[(Double, Double)](slots.size)
    clusters.foreach { (centre, members) =>
      if members.size == 1 then relocated(members.head) = centre
      else
        val offset = minSeparation / 2
        members.zipWithIndex.foreach { (originalIndex, rank) =>
          val angle = 2 * math.Pi * rank / members.size
          relocated(originalIndex) =
            (centre._1 + math.sin(angle) * offset, centre._2 + math.cos(angle) * offset)
        }
    }
    relocated.toList

  /** The anchor stands on the origin and takes no slot, so nothing may be sent to stand on it. */
  def nudgeOffAnchor(slots: List[(Double, Double)], minRadius: Double): List[(Double, Double)] =
    val count = slots.size
    slots.zipWithIndex.map { case ((x, y), index) =>
      val radius = math.hypot(x, y)
      if radius >= minRadius then (x, y)
      else if radius < Epsilon then
        val angle = if count == 0 then 0.0 else 2 * math.Pi * index / count
        (math.sin(angle) * minRadius, math.cos(angle) * minRadius)
      else
        val factor = minRadius / radius
        (x * factor, y * factor)
    }

  def cartesian(x: Formula, y: Formula, ctx: SlotContext): List[(Double, Double)] =
    val phase = Formula.wrapPhase(ctx.phase)
    val n = ctx.count.toDouble
    (0 until ctx.count).map { i =>
      (Formula.evaluate(x, i.toDouble, n, phase), Formula.evaluate(y, i.toDouble, n, phase))
    }.toList

  /** In [[ShapeFormation.ring]]'s bearing convention. */
  def polar(r: Formula, theta: Formula, ctx: SlotContext): List[(Double, Double)] =
    val phase = Formula.wrapPhase(ctx.phase)
    val n = ctx.count.toDouble
    (0 until ctx.count).map { i =>
      val radius = Formula.evaluate(r, i.toDouble, n, phase)
      val bearing = Formula.evaluate(theta, i.toDouble, n, phase)
      (math.sin(bearing) * radius, math.cos(bearing) * radius)
    }.toList

  private def distance(a: (Double, Double), b: (Double, Double)): Double =
    math.hypot(a._1 - b._1, a._2 - b._2)

  /** The point at arc length `target`, interpolated inside its segment. */
  private def pointAt(
      vertices: IndexedSeq[(Double, Double)],
      lengths: IndexedSeq[Double],
      closed: Boolean,
      target: Double
  ): (Double, Double) =
    val segments = if closed then vertices.size else vertices.size - 1
    var segment = 0
    while segment < segments - 1 && lengths(segment + 1) < target do segment += 1
    val from = vertices(segment)
    // The modulo walks a closed path's wrap segment; an open path never reaches it, and the
    // clamped `segment` keeps float overshoot from indexing past the last vertex.
    val to = vertices((segment + 1) % vertices.size)
    val spanStart = lengths(segment)
    val span = lengths(segment + 1) - spanStart
    if span < Epsilon then from
    else
      val fraction = math.min(1.0, math.max(0.0, (target - spanStart) / span))
      (from._1 + (to._1 - from._1) * fraction, from._2 + (to._2 - from._2) * fraction)

/**
 * Geometry as data on the retained `/config/formation` message: a new shape costs no restart.
 *
 * `slots` may call only `sense`, never `rep`, `share`, `nbr`, `branch` or `foldhood`. That is what
 * keeps a spec out of the export's shape, so devices briefly holding different specs still align
 * and the fleet follows the root's.
 */
class CustomFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val spec = sense[CustomSpec](CustomFormation.SPEC_SENSING)
    val scale = sense[Double](CustomFormation.SCALE_SENSING)
    val maxRadius = sense[Double](CustomFormation.MAX_RADIUS_SENSING)
    val gap = clearance
    val layout = (phase: Double) => CustomSlots.slotsFor(spec, ctx.copy(phase = phase), scale, maxRadius, gap)
    layout(phaseFor(CustomSlots.travel(layout))).getOrElse(fallback(ctx))

  /**
   * A ring, not a hold: an empty slot list makes every robot pivot to the reference heading, which
   * looks identical to a lost leader, and leaves the G/C/G path cold so the first real spec lands
   * with a visible transient. A ring says "running, nobody has told me a shape yet".
   */
  private def fallback(ctx: SlotContext): List[(Double, Double)] =
    val radius = math.max(sense[Double](CircleFormation.RADIUS_SENSING), ShapeFormation.minRingRadius(ctx.count, clearance))
    ShapeFormation.ring(ctx.count, 0.0)(_ => radius)

object CustomFormation:
  /**
   * The compiled spec, and the one molecule that is not a `Double`. `sense` is an unchecked cast,
   * so the default below must be a real [[CustomSpec]] or every robot throws on its first round.
   */
  val SPEC_SENSING = "customSpec"

  /** Live multiplier on a spec, so an operator can resize a shape without a new spec. */
  val SCALE_SENSING = "customScale"

  /** Metres. On the same message as the spec, so it guards the mistaken author, not the malicious
    * one; [[CustomSlots.AbsoluteMaxRadius]] is the bound no message can raise.
    */
  val MAX_RADIUS_SENSING = "customMaxRadius"

  val DEFAULTS: Map[String, Any] = Map(
    SPEC_SENSING -> CustomSpec.Absent,
    SCALE_SENSING -> 1.0,
    MAX_RADIUS_SENSING -> 1.5
  )
