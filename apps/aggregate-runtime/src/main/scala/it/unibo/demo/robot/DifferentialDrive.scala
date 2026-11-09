package it.unibo.demo.robot

/**
 * `minDutyCycle` and `firmwareEmaAlpha` mirror `MotorConfig::default()` in the DropBot firmware
 * (`src/drivers/motor_driver/types.rs`); the controller inverts them. Keep them in sync.
 *
 * @param minCommand the firmware's stiction floor is one fleet-wide constant and the real robots
 *                   do not all start at it, so stay clear of wherever the true threshold sits
 */
final case class DriveConfig(
    wheelBaseM: Double = 0.10,
    maxLinearSpeedMs: Double = 0.35,
    minDutyCycle: Double = 0.30,
    minCommand: Double = 0.10,
    firmwareEmaAlpha: Double = 0.10,
    markerYawOffsetRad: Double = math.Pi / 2,
    invertWheels: Boolean = false
):
  require(wheelBaseM > 0, "wheelBaseM must be positive")
  require(maxLinearSpeedMs > 0, "maxLinearSpeedMs must be positive")
  require(minDutyCycle >= 0 && minDutyCycle < 1, "minDutyCycle must be in [0, 1)")
  require(minCommand >= 0 && minCommand < 1, "minCommand must be in [0, 1)")
  require(firmwareEmaAlpha > 0 && firmwareEmaAlpha <= 1, "firmwareEmaAlpha must be in (0, 1]")

  val maxAngularSpeedRadS: Double = 2.0 * maxLinearSpeedMs / wheelBaseM

  /** Slowest wheel speed reachable: what `minCommand` produces. Below it the wheel must stop. */
  val minWheelFraction: Double = minDutyCycle + minCommand * (1.0 - minDutyCycle)
  val minLinearSpeedMs: Double = minWheelFraction * maxLinearSpeedMs
  val minAngularSpeedRadS: Double = minWheelFraction * maxAngularSpeedRadS

object DriveConfig:

  private def env(name: String): Option[String] =
    Option(System.getenv(name)).map(_.trim).filter(_.nonEmpty)

  private def envDouble(name: String, fallback: Double): Double =
    env(name).flatMap(_.toDoubleOption).getOrElse(fallback)

  private def envBoolean(name: String, fallback: Boolean): Boolean =
    env(name).map(_.toLowerCase).map(v => v == "true" || v == "1" || v == "yes").getOrElse(fallback)

  /** Field calibration by restart, not rebuild. `CalibrateDrive` measures the frame conventions. */
  lazy val fromEnvironment: DriveConfig =
    val defaults = DriveConfig()
    DriveConfig(
      wheelBaseM = envDouble("DRIVE_WHEEL_BASE_M", defaults.wheelBaseM),
      maxLinearSpeedMs = envDouble("DRIVE_MAX_SPEED_MS", defaults.maxLinearSpeedMs),
      minDutyCycle = envDouble("DRIVE_MIN_DUTY", defaults.minDutyCycle),
      minCommand = envDouble("DRIVE_MIN_COMMAND", defaults.minCommand),
      firmwareEmaAlpha = envDouble("DRIVE_FIRMWARE_EMA_ALPHA", defaults.firmwareEmaAlpha),
      markerYawOffsetRad =
        math.toRadians(envDouble("DRIVE_MARKER_YAW_OFFSET_DEG", math.toDegrees(defaults.markerYawOffsetRad))),
      invertWheels = envBoolean("DRIVE_INVERT_WHEELS", defaults.invertWheels)
    )

/** The only place that knows how m/s and rad/s become `left`/`right` on the wire. */
object DifferentialDrive:

  private val negligibleWheelFraction = 0.02

  def normalizeAngle(angle: Double): Double =
    if angle.isNaN then Double.NaN
    else
      val wrapped = math.IEEEremainder(angle, 2 * math.Pi)
      if wrapped <= -math.Pi then wrapped + 2 * math.Pi
      else if wrapped > math.Pi then wrapped - 2 * math.Pi
      else wrapped

  def bodyHeading(markerYawRad: Double, config: DriveConfig): Double =
    normalizeAngle(markerYawRad + config.markerYawOffsetRad)

  def headingVector(markerYawRad: Double, config: DriveConfig): (Double, Double) =
    val heading = bodyHeading(markerYawRad, config)
    (math.cos(heading), math.sin(heading))

  private def clamp(value: Double, limit: Double): Double =
    math.max(-limit, math.min(limit, value))

  /**
   * Body twist -> `(left, right)` in `[-1, 1]`. Turn rate wins: forward speed gives up the headroom,
   * so the pair fits without clipping - clipping wheels independently, as the firmware would,
   * straightens the robot out exactly when it is turning hardest.
   */
  def toWheels(linearMs: Double, angularRadS: Double, config: DriveConfig): (Double, Double) =
    if linearMs.isNaN || angularRadS.isNaN then (0.0, 0.0)
    else
      val turn = clamp(angularRadS * config.wheelBaseM / (2.0 * config.maxLinearSpeedMs), 1.0)
      val forward = clamp(linearMs / config.maxLinearSpeedMs, 1.0 - math.abs(turn))
      val (left, right) = reachablePair(forward - turn, forward + turn, config)
      val leftCommand = toCommand(left, config)
      val rightCommand = toCommand(right, config)
      if config.invertWheels then (rightCommand, leftCommand) else (leftCommand, rightCommand)

  private def isReachable(wheelFraction: Double, config: DriveConfig): Boolean =
    wheelFraction == 0.0 || math.abs(wheelFraction) >= config.minWheelFraction

  /** Cheapest concession first: scale (same path), shift (same turn rate), spin (aiming last). */
  private def reachablePair(left: Double, right: Double, config: DriveConfig): (Double, Double) =
    val peak = math.max(math.abs(left), math.abs(right))
    if peak < negligibleWheelFraction then (0.0, 0.0)
    else if isReachable(left, config) && isReachable(right, config) then (left, right)
    else
      val trough = math.min(math.abs(left), math.abs(right))
      val scale = config.minWheelFraction / (if trough > 0.0 then trough else peak)
      val scaled = (left * scale, right * scale)
      if fits(scaled) then scaled
      else
        val slower = if math.abs(left) <= math.abs(right) then left else right
        val lift = math.signum(slower) * (config.minWheelFraction - trough)
        val shifted = (left + lift, right + lift)
        if fits(shifted) && isReachable(shifted._1, config) && isReachable(shifted._2, config) then shifted
        else
          val half = (right - left) / 2.0
          if math.abs(half) < config.minWheelFraction then
            val spin = math.signum(half) * config.minWheelFraction
            (-spin, spin)
          else (-half, half)

  private def fits(pair: (Double, Double)): Boolean =
    math.max(math.abs(pair._1), math.abs(pair._2)) <= 1.0

  /** Invert the firmware's stiction remap; the floor only guards against leaking `(0, minCommand)`. */
  private def toCommand(wheelFraction: Double, config: DriveConfig): Double =
    val magnitude = math.min(1.0, math.abs(wheelFraction))
    if magnitude <= 0.0 then 0.0
    else
      val command = (magnitude - config.minDutyCycle) / (1.0 - config.minDutyCycle)
      math.signum(wheelFraction) * math.max(config.minCommand, math.min(1.0, command))

  /** What the firmware actually drives for `command`. Used by tests and diagnostics. */
  def firmwareDutyCycle(command: Double, config: DriveConfig): Double =
    val magnitude = math.min(1.0, math.abs(command))
    if magnitude <= 0.0 then 0.0
    else config.minDutyCycle + magnitude * (1.0 - config.minDutyCycle)
