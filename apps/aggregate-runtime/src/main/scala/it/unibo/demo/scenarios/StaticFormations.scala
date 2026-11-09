package it.unibo.demo.scenarios

class LineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    ShapeFormation.lineOffsets(ctx.count, spacing).map(x => (x, 0.0))

  private def spacing: Double = math.max(sense(LineFormation.INTER_DISTANCE_SENSING), clearance)

object LineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceLine"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.2)

class VerticalLineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    (1 to ctx.count).map(k => (0.0, -k * spacing)).toList

  private def spacing: Double = math.max(sense(VerticalLineFormation.INTER_DISTANCE_SENSING), clearance)

object VerticalLineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceVertical"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.2)

class CircleFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val floor = ShapeFormation.minRingRadius(ctx.count, clearance)
    ShapeFormation.ring(ctx.count, 0.0)(_ => math.max(radius, floor))

  private def radius: Double = sense(CircleFormation.RADIUS_SENSING)

object CircleFormation:
  val RADIUS_SENSING: String = "radius"
  val DEFAULTS: Map[String, Double] = Map(RADIUS_SENSING -> 0.35)

class SquareFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    SquareFormation.grid(ctx.count, spacing)

  private def spacing: Double = math.max(sense(SquareFormation.INTER_DISTANCE_SENSING), clearance)

object SquareFormation:
  val INTER_DISTANCE_SENSING = "interDistanceSquare"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.2)

  /** A square grid of `count` cells, growing by one to skip the centre cell held by the anchor. */
  def grid(count: Int, spacing: Double): List[(Double, Double)] =
    if count <= 0 then List.empty
    else
      val side = math.ceil(math.sqrt(count + 1)).toInt
      (for
        y <- 0 until side
        x <- 0 until side if !(x == 0 && y == 0)
      yield (x * spacing, y * spacing)).take(count).toList

class VFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    // The shared reference, not `orientation`: that one tilts the V differently on every device.
    ShapeFormation.rotated(arms(ctx.count), ctx.reference)

  private def arms(count: Int): List[(Double, Double)] =
    val dx = spacing * math.cos(armAngle)
    val dy = spacing * math.sin(armAngle)
    val left = count / 2
    ((1 to left).map(k => (-k * dx, k * dy)) ++ (1 to count - left).map(k => (k * dx, k * dy))).toList

  private def spacing: Double = math.max(sense(VFormation.INTER_DISTANCE_SENSING), clearance)
  private def armAngle: Double = sense(VFormation.ANGLE_SENSING)

object VFormation:
  val INTER_DISTANCE_SENSING = "interDistanceV"
  val ANGLE_SENSING = "angleV"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.2, ANGLE_SENSING -> -Math.PI / 4)

class HeartFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val unit = HeartFormation.curve(ctx.count, 1.0)
    val k = math.max(scale, clearance / ShapeFormation.tightestPair((0.0, 0.0) :: unit))
    unit.map((x, y) => (x * k, y * k))

  private def scale: Double = sense(HeartFormation.SCALE_SENSING)

object HeartFormation:
  val SCALE_SENSING = "scaleHeart"
  val DEFAULTS = Map(SCALE_SENSING -> 0.02)

  /** The parametric heart at unit scale, from its bottom cusp on the origin once around. */
  private val Outline: List[(Double, Double)] = (0 until 400).map { k =>
    val t = -math.Pi + 2 * math.Pi * k / 400
    val sinT = math.sin(t)
    val x = 16 * sinT * sinT * sinT
    val y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
    (x, y + 17.0)
  }.toList

  /**
   * `count` slots at equal arc length, the cusp left to the anchor. An even fleet would straddle
   * the top notch, two slots a few centimetres apart, so it leaves the notch itself empty instead.
   */
  def curve(count: Int, scale: Double): List[(Double, Double)] =
    if count <= 0 then List.empty
    else
      val even = count % 2 == 0
      val samples = CustomSlots.resample(Outline, closed = true, if even then count + 2 else count + 1)
      val kept = if even then samples.patch((count + 2) / 2, Nil, 1) else samples
      kept.tail.map((x, y) => (x * scale, y * scale))
