package it.unibo.demo.scenarios

/**
 * What the dashboard published under `custom`, already validated and compiled.
 *
 * Compiled at the edge (see [[it.unibo.demo.provider.CustomSpecCodec]]) and stored in the config
 * map as a single value, for three reasons: a formation round then does one `sense`, no parsing
 * and no validation; a spec is applied whole, so an `x` from the new message can never be paired
 * with a `y` from the old one; and a rejection is representable, so the runtime can fall back and
 * log rather than throw inside a round.
 */
enum CustomSpec:
  /** No `custom` object was ever published. */
  case Absent

  /** One was, and it was rejected. `reason` is what to show its author. */
  case Invalid(reason: String)

  /**
   * An explicit path through `points`, in metres, with the anchor at the origin, resampled to the
   * fleet size at equal arc length.
   */
  case Points(points: List[(Double, Double)], closed: Boolean)

  /** Cartesian formulas, evaluated once per slot. */
  case Cartesian(x: Formula, y: Formula)

  /**
   * Polar formulas. `theta` follows the bearing convention of [[ShapeFormation.ring]] exactly --
   * x from the sine and y from the cosine, so zero is straight ahead and the bearing advances
   * clockwise -- which is what makes a constant radius with an evenly divided angle reproduce
   * [[CircleFormation]] to the last bit.
   */
  case Polar(r: Formula, theta: Formula)

object CustomSpec:
  /** Longest point list a payload may carry, before resampling. */
  val MaxPoints: Int = 256

/**
 * Turns a [[CustomSpec]] into a slot set a fleet can actually execute.
 *
 * Every function here is pure and total, and the whole pipeline is exercised by
 * `CustomSlotsSuite` without standing up an aggregate round -- the same split
 * [[FormationGeometrySuite]] already relies on.
 */
object CustomSlots:

  /**
   * No payload can put a slot further than this from the anchor, whatever it says. Applied inside
   * [[slotsFor]] rather than by the caller, so the ceiling is part of the tested contract rather
   * than a discipline every caller has to remember.
   */
  val AbsoluteMaxRadius: Double = 3.0

  /** Two points closer than this are the same point. */
  val Epsilon: Double = 1e-6

  /** Bounds on the operator's live scale knob, so it cannot itself become the hazard. */
  val MinScale: Double = 0.05
  val MaxScale: Double = 10.0

  /**
   * How far apart two slots must be to be physically distinct.
   *
   * The floor is `BaseDemo.CollisionArea`, the radius inside which [[ShapeFormation]]'s repulsion
   * already pushes robots apart, so two slots at exactly this separation are the closest pair the
   * controller will hold without fighting itself.
   */
  def minSeparation(collisionArea: Double): Double =
    if !collisionArea.isFinite then ShapeFormation.MinRadius
    else math.max(ShapeFormation.MinRadius, collisionArea)

  /**
   * The slot set `spec` asks for, for a fleet of `ctx.count`.
   *
   * `None` means "this spec cannot produce a shape" -- absent or rejected -- and leaves the
   * fallback policy to [[CustomFormation]]. `Some` is guaranteed to hold exactly `ctx.count`
   * entries with every coordinate finite.
   *
   * The exact-count guarantee is load-bearing: `AssignmentSolver.solveIndices` returns an empty
   * map on any size mismatch, so a wrong-length slot list freezes the whole fleet without a
   * single log line anywhere.
   */
  def slotsFor(
      spec: CustomSpec,
      ctx: SlotContext,
      scale: Double,
      maxRadius: Double,
      collisionArea: Double
  ): Option[List[(Double, Double)]] =
    if ctx.count <= 0 then Some(List.empty)
    else
      spec match
        case CustomSpec.Absent | CustomSpec.Invalid(_) => None
        case _ =>
          val cap =
            if maxRadius.isFinite then math.min(math.max(maxRadius, ShapeFormation.MinRadius), AbsoluteMaxRadius)
            else AbsoluteMaxRadius
          val gap = minSeparation(collisionArea)
          val k =
            if scale.isFinite then math.min(MaxScale, math.max(MinScale, scale)) else 1.0
          // A path also reports the spacing it was resampled at, which is what decides whether the
          // shape is too small for the fleet; the formula modes have no such uniform spacing.
          val (raw, pathGap) = spec match
            case CustomSpec.Points(points, closed) =>
              val path = dedupeConsecutive(points)
              (resample(path, closed, ctx.count), Some(pathSpacing(path, closed, ctx.count)))
            case CustomSpec.Cartesian(x, y) => (cartesian(x, y, ctx), None)
            case CustomSpec.Polar(r, theta) => (polar(r, theta, ctx), None)
            case _ => (List.empty, None)
          // Coercing non-finite coordinates first matters: a runaway infinity becomes the origin
          // and is then handled as a coincident slot, rather than dominating the growth factor.
          val scaled = finite(raw).map((x, y) => (x * k, y * k))
          val grown = pathGap match
            case Some(spacing) => growToFit(scaled, spacing * k, gap, cap)
            case None => scaled
          Some(nudgeOffAnchor(separateCoincident(clampRadius(grown, cap), gap), gap / 2))

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

  /**
   * `count` points at equal arc length along `points`.
   *
   * Read as an open polyline unless `closed`, in which case the wrap segment is walked too and the
   * seam carries no duplicate slot. Open sampling puts a slot on each end, so an evenly spaced
   * input resampled to its own length comes back unchanged -- the least-surprise behaviour for an
   * author who wrote one point per robot. An open default matters because closing every path would
   * turn "a line of five points" into a there-and-back trip with two robots per position; a ring
   * is easy to ask for instead, by setting `closed` or repeating the first point at the end.
   *
   * Subsampling does not preserve corners. That is the intended trade: even spacing keeps a
   * physical fleet clear of itself, whereas a corner-preserving simplification leaves gaps in one
   * region and a pile-up in another.
   *
   * A degenerate path -- one point, or every point within [[Epsilon]] -- yields `count` copies of
   * that point; [[separateCoincident]] then fans them out.
   */
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
   * Radii clamped to `maxRadius`, each along its own bearing.
   *
   * Per slot, not a uniform shrink of the whole set. A uniform shrink would preserve proportions,
   * which is nicer for a path written in the wrong unit -- but it couples the slots: one runaway
   * value from a formula would collapse the entire formation to a dot. Per-slot clamping keeps the
   * failure local to the slot that caused it, and the unit-mistake case is already covered by
   * [[growToFit]] and by the operator's scale knob.
   */
  def clampRadius(slots: List[(Double, Double)], maxRadius: Double): List[(Double, Double)] =
    slots.map { (x, y) =>
      val radius = math.hypot(x, y)
      if radius <= maxRadius || radius < Epsilon then (x, y)
      else
        val factor = maxRadius / radius
        (x * factor, y * factor)
    }

  /**
   * The arc-length gap between consecutive slots a path will be resampled at.
   *
   * Uniform by construction, which is exactly what the chord between two consecutive slots is
   * not: at a corner the straight line between them is far shorter than the distance walked along
   * the path, and that difference says nothing about whether the fleet has room.
   */
  def pathSpacing(points: List[(Double, Double)], closed: Boolean, count: Int): Double =
    if count <= 1 then 0.0
    else
      val vertices = points.toIndexedSeq
      if vertices.size < 2 then 0.0
      else
        val total = arcLengths(vertices, closed).last
        if closed then total / count else total / (count - 1)

  /**
   * Uniformly grows a slot set until neighbouring slots are `minSpacing` apart, never beyond
   * `maxRadius`, and never shrinking.
   *
   * The single most likely authoring mistake is a shape too small for the fleet to stand on -- a
   * 20cm triangle for nine robots. A uniform scale fixes that while preserving the requested shape
   * exactly.
   *
   * `spacing` is the caller's measure of how far apart neighbouring slots actually are, and for a
   * path it must be the arc-length spacing rather than the shortest chord. Measuring the shortest
   * chord conflates a corner with overcrowding: on a triangle sampled every 0.41 m, the two slots
   * either side of a vertex sit 0.19 m apart in a straight line, which read as "too small" and
   * inflated the whole triangle by half again. That is the same trap this function documents for
   * the formula modes, which is why they do not use it -- their spacing is not uniform, so there
   * is no honest single number to grow by. A genuine pinch at one corner is left to the collision
   * repulsion, which exists for precisely that.
   */
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
      // Growing must not breach the radius cap, and must never shrink: a shape already inside the
      // cap but wider than it stays exactly as the author wrote it.
      val allowed = if furthest < Epsilon then wanted else maxRadius / furthest
      val factor = math.max(1.0, math.min(wanted, allowed))
      slots.map((x, y) => (x * factor, y * factor))

  /**
   * Slots within [[Epsilon]] of each other fanned onto a ring of radius `minSeparation / 2` about
   * their common position, so a group of coincident slots ends up `minSeparation` apart rather
   * than stacked. Bearings come from the position within the group, so the result is
   * deterministic.
   *
   * Runs after [[clampRadius]] on purpose: clamping itself creates coincidence, since two slots on
   * the same bearing at five and ten metres both land on the cap.
   */
  def separateCoincident(
      slots: List[(Double, Double)],
      minSeparation: Double
  ): List[(Double, Double)] =
    // Cluster by proximity in one sweep. Grouping on a rounded grid instead would miss a pair
    // that straddles a cell boundary and leave those two robots stacked; at fleet sizes of a few
    // dozen the quadratic sweep costs nothing worth saving.
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

  /**
   * Any slot inside `minRadius` of the origin pushed out to `minRadius`, on the bearing its
   * position in the list would have on a ring.
   *
   * The anchor robot stands on the origin and takes no slot of its own, so nothing may be sent to
   * stand on it.
   */
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

  /** Formula slots, cartesian. `t` is [[Formula.wrapPhase]] of the context's phase. */
  def cartesian(x: Formula, y: Formula, ctx: SlotContext): List[(Double, Double)] =
    val phase = Formula.wrapPhase(ctx.phase)
    val n = ctx.count.toDouble
    (0 until ctx.count).map { i =>
      (Formula.evaluate(x, i.toDouble, n, phase), Formula.evaluate(y, i.toDouble, n, phase))
    }.toList

  /** Formula slots, polar, in [[ShapeFormation.ring]]'s bearing convention. */
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

  /** The point `target` along the polyline, by linear interpolation inside its segment. */
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
    // The modulo is what walks the wrap segment of a closed path; on an open one the index can
    // never reach it. Clamping `segment` above also stops float overshoot at the very end of the
    // path from indexing past the last vertex.
    val to = vertices((segment + 1) % vertices.size)
    val spanStart = lengths(segment)
    val span = lengths(segment + 1) - spanStart
    if span < Epsilon then from
    else
      val fraction = math.min(1.0, math.max(0.0, (target - spanStart) / span))
      (from._1 + (to._1 - from._1) * fraction, from._2 + (to._2 - from._2) * fraction)

/**
 * A formation whose geometry arrives as data, on the same retained `/config/formation` message as
 * everything else, so a shape the runtime has never seen costs no recompilation and no restart.
 *
 * Like every [[ShapeFormation]], `slots` here calls only `sense`, which is alignment-neutral. It
 * must never call `rep`, `share`, `nbr`, `branch` or `foldhood`: only the root evaluates `slots`,
 * and the whole reason a spec need not participate in alignment is that its content cannot reach
 * the export's shape. Two devices briefly holding different specs still align perfectly and the
 * fleet follows whichever one the root holds.
 */
class CustomFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    CustomSlots
      .slotsFor(
        sense[CustomSpec](CustomFormation.SPEC_SENSING),
        ctx,
        scale = sense[Double](CustomFormation.SCALE_SENSING),
        maxRadius = sense[Double](CustomFormation.MAX_RADIUS_SENSING),
        collisionArea = sense[Double](BaseDemo.CollisionArea)
      )
      .getOrElse(fallback(ctx))

  /**
   * What stands in when no usable spec was ever published: the plain ring, not a hold.
   *
   * A hold is the wrong answer on three counts. An empty slot list is not even a hold --
   * `AssignmentSolver` yields no displacement, `actuate` sees a zero goal, and every non-root
   * robot pivots to the reference heading, which on a demo floor reads as a malfunction. It is
   * also indistinguishable from a lost leader or a failed round, which is the worst possible
   * diagnostic. And it leaves the collect/assign/broadcast path cold, so the first good spec
   * arrives with a visible transient. A ring says "running, nobody has told me a shape yet",
   * reuses the radius the dashboard already has a slider for, and keeps the whole plan warm.
   */
  private def fallback(ctx: SlotContext): List[(Double, Double)] =
    ShapeFormation.ring(ctx.count, 0.0)(_ => sense[Double](CircleFormation.RADIUS_SENSING))

object CustomFormation:
  /**
   * The compiled spec.
   *
   * Unlike every other molecule this is not a `Double`: the config map is `Map[String, Any]` and
   * `sense` is an unchecked cast, so the default below must be a real [[CustomSpec]] -- a `String`
   * there would give every robot a `ClassCastException` on its first round.
   */
  val SPEC_SENSING = "customSpec"

  /** Live multiplier on a spec, so an operator can resize a shape without a new spec. */
  val SCALE_SENSING = "customScale"

  /**
   * Operator-facing cap on how far a slot may sit from the anchor, in metres.
   *
   * It travels on the same message as the spec, so it guards against a mistaken author rather than
   * a malicious one; [[CustomSlots.AbsoluteMaxRadius]] is the bound no message can raise.
   */
  val MAX_RADIUS_SENSING = "customMaxRadius"

  val DEFAULTS: Map[String, Any] = Map(
    SPEC_SENSING -> CustomSpec.Absent,
    SCALE_SENSING -> 1.0,
    MAX_RADIUS_SENSING -> 1.5
  )
