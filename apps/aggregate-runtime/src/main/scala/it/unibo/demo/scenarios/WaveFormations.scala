package it.unibo.demo.scenarios

/** A ring that turns: the slots keep their spacing but their bearing advances with time. */
class OrbitFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val radius = sense[Double](CircleFormation.RADIUS_SENSING)
    ShapeFormation.ring(ctx.count, ctx.phase)(_ => radius)

/** A ring that breathes: every slot's radius pulses in unison. */
class BreathingCircleFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val rest = sense[Double](CircleFormation.RADIUS_SENSING)
    val amplitude = sense[Double](ShapeFormation.WaveAmplitude)
    val radius = math.max(ShapeFormation.MinRadius, rest + amplitude * math.sin(ctx.phase))
    ShapeFormation.ring(ctx.count, 0.0)(_ => radius)

/** A travelling wave over the ring: the crest runs around the formation. */
class RingWaveFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    val rest = sense[Double](CircleFormation.RADIUS_SENSING)
    val amplitude = sense[Double](ShapeFormation.WaveAmplitude)
    val waveNumber = sense[Double](ShapeFormation.WaveNumber)
    ShapeFormation.ring(ctx.count, 0.0) { i =>
      val spatial = 2 * math.Pi * waveNumber * i / ctx.count
      math.max(ShapeFormation.MinRadius, rest + amplitude * math.sin(ctx.phase - spatial))
    }

/** A sine wave standing on a line: the robots hold their spacing and ride the wave across it. */
class SineLineFormation extends ShapeFormation():
  override protected def slots(ctx: SlotContext): List[(Double, Double)] =
    SineLineFormation.wave(
      ctx.count,
      sense[Double](LineFormation.INTER_DISTANCE_SENSING),
      sense[Double](ShapeFormation.WaveAmplitude),
      sense[Double](ShapeFormation.WaveNumber),
      ctx.phase
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
