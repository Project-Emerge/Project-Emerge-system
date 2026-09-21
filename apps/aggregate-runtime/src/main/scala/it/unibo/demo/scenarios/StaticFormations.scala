package it.unibo.demo.scenarios

class LineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    ShapeFormation.lineOffsets(ctx.count, spacing).map(x => (x, 0.0))

  private def spacing: Double = sense(LineFormation.INTER_DISTANCE_SENSING)

object LineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceLine"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)

class VerticalLineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    (1 to ctx.count).map(k => (0.0, -k * spacing)).toList

  private def spacing: Double = sense(VerticalLineFormation.INTER_DISTANCE_SENSING)

object VerticalLineFormation:
  val INTER_DISTANCE_SENSING = "interDistanceVertical"
  val DEFAULTS = Map(INTER_DISTANCE_SENSING -> 0.4)

class CircleFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    ShapeFormation.ring(ctx.count, 0.0)(_ => radius)

  private def radius: Double = sense(CircleFormation.RADIUS_SENSING)

object CircleFormation:
  val RADIUS_SENSING: String = "radius"
  val DEFAULTS: Map[String, Double] = Map(RADIUS_SENSING -> 0.6)

class SquareFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    SquareFormation.grid(ctx.count, spacing)

  private def spacing: Double = sense(SquareFormation.INTER_DISTANCE_SENSING)

object SquareFormation:
  val INTER_DISTANCE_SENSING = "interDistanceSquare"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4)

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

  private def spacing: Double = sense(VFormation.INTER_DISTANCE_SENSING)
  private def armAngle: Double = sense(VFormation.ANGLE_SENSING)

object VFormation:
  val INTER_DISTANCE_SENSING = "interDistanceV"
  val ANGLE_SENSING = "angleV"
  val DEFAULTS: Map[String, Double] = Map(INTER_DISTANCE_SENSING -> 0.4, ANGLE_SENSING -> -Math.PI / 4)

class HeartFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    HeartFormation.curve(ctx.count, scale)

  private def scale: Double = sense(HeartFormation.SCALE_SENSING)

object HeartFormation:
  val SCALE_SENSING = "scaleHeart"
  val DEFAULTS = Map(SCALE_SENSING -> 0.06)

  /** The parametric heart curve, shifted so its bottom cusp -- left to the anchor -- is the origin. */
  def curve(count: Int, scale: Double): List[(Double, Double)] =
    if count <= 0 then List.empty
    else
      val span = count + 1
      (0 until count).map { i =>
        val t = -math.Pi + (2.0 * math.Pi * (i + 1).toDouble) / span.toDouble
        val sinT = math.sin(t)
        val x = 16 * sinT * sinT * sinT
        val y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        (x * scale, (y + 17.0) * scale)
      }.toList
