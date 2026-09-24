package it.unibo.demo.scenarios

/** A ring that turns: the slots keep their spacing but their bearing advances with time. */
class OrbitFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val radius = math.max(sense[Double](CircleFormation.RADIUS_SENSING), ShapeFormation.minRingRadius(ctx.count, clearance))
    ShapeFormation.ring(ctx.count, phaseFor(radius))(_ => radius)

/** A ring that breathes: every slot's radius pulses in unison. */
class BreathingCircleFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val amplitude = sense[Double](ShapeFormation.WaveAmplitude)
    val rest = BreathingCircleFormation.rest(sense[Double](CircleFormation.RADIUS_SENSING), amplitude, ctx.count, clearance)
    val radius = rest + amplitude * math.sin(phaseFor(amplitude))
    ShapeFormation.ring(ctx.count, 0.0)(_ => radius)

object BreathingCircleFormation:
  /** Raised rather than clipped, so the ring keeps its full swing without its tightest point
    * ever pinching below the clearance: a clipped ring would stop breathing for half of every cycle.
    */
  def rest(radius: Double, amplitude: Double, count: Int, clearance: Double): Double =
    math.max(radius, ShapeFormation.minRingRadius(count, clearance) + math.abs(amplitude))

/** A travelling wave over the ring: the crest runs around the formation. */
class RingWaveFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val amplitude = sense[Double](ShapeFormation.WaveAmplitude)
    val rest = BreathingCircleFormation.rest(sense[Double](CircleFormation.RADIUS_SENSING), amplitude, ctx.count, clearance)
    val waveNumber = sense[Double](ShapeFormation.WaveNumber)
    val now = phaseFor(amplitude)
    ShapeFormation.ring(ctx.count, 0.0) { i =>
      val spatial = 2 * math.Pi * waveNumber * i / ctx.count
      rest + amplitude * math.sin(now - spatial)
    }

/** A sine wave standing on a line: the robots hold their spacing and ride the wave across it. */
class SineLineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val amplitude = sense[Double](ShapeFormation.WaveAmplitude)
    SineLineFormation.wave(
      ctx.count,
      math.max(sense[Double](LineFormation.INTER_DISTANCE_SENSING), clearance),
      amplitude,
      sense[Double](ShapeFormation.WaveNumber),
      phaseFor(amplitude)
    )

object SineLineFormation:
  /** `waveNumber` crests fit across the whole line, and they travel with `phase`. */
  def wave(
      count: Int,
      spacing: Double,
      amplitude: Double,
      waveNumber: Double,
      phase: Double
  ): List[(Double, Double)] =
    val span = math.max(spacing * count, 1e-9)
    ShapeFormation.lineOffsets(count, spacing).map { x =>
      val spatial = 2 * math.Pi * waveNumber * x / span
      (x, amplitude * math.sin(phase - spatial))
    }
