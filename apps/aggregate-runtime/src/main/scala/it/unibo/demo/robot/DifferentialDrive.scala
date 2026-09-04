package it.unibo.demo.robot

/**
 * Physical parameters of the differential-drive platform, plus the two frame conventions that
 * relate a vision pose to the robot body.
 *
 * The defaults mirror the real plant:
 *   - `wheelBaseM` / `maxLinearSpeedMs` match the kinematics used by `apps/robot-emulator`.
 *   - `minDutyCycle` and `firmwareEmaAlpha` mirror `MotorConfig::default()` in the DropBot
 *     firmware (`src/drivers/motor_driver/types.rs`). They are *not* tuning knobs: they describe
 *     the actuator, and the controller inverts them. If the firmware changes, change them here.
 *
 * @param wheelBaseM         distance between the two wheels, metres
 * @param maxLinearSpeedMs   wheel speed at 100% duty cycle, m/s
 * @param minDutyCycle       lowest duty cycle the firmware ever drives for a non-zero command;
 *                           below it the motor stalls, so wheel speeds in `(0, minDutyCycle)`
 *                           are physically unreachable
 * @param minCommand         smallest command magnitude ever published. The firmware's own stiction
 *                           floor is meant to make even a tiny command turn the wheel, but that
 *                           floor is a single fleet-wide constant and the real robots do not all
 *                           start at it: near-zero commands are observed to leave them humming
 *                           instead of moving. Refusing to publish anything below this keeps every
 *                           command comfortably clear of wherever the true threshold sits
 * @param firmwareEmaAlpha   the firmware's per-command EMA coefficient; `1.0` disables the
 *                           lag compensation in [[MotorCommandPublisher]]
 * @param markerYawOffsetRad rotation from the ArUco marker frame to the robot's forward axis.
 *                           `Pi/2` means "body forward is the marker's local +Y axis"
 * @param invertWheels       swap `left`/`right` in the published payload
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

  /** Fastest in-place rotation the platform can execute, rad/s (both wheels at full opposite speed). */
  val maxAngularSpeedRadS: Double = 2.0 * maxLinearSpeedMs / wheelBaseM

  /**
   * Slowest wheel speed we will ask for, as a fraction of full speed: the duty cycle that
   * [[minCommand]] produces. Wheel speeds between zero and this are not reachable - the wheel is
   * either stopped or turning at least this fast.
   */
  val minWheelFraction: Double = minDutyCycle + minCommand * (1.0 - minDutyCycle)

  /** Slowest linear speed the platform can hold, m/s. */
  val minLinearSpeedMs: Double = minWheelFraction * maxLinearSpeedMs

  /** Slowest in-place rotation the platform can hold, rad/s. */
  val minAngularSpeedRadS: Double = minWheelFraction * maxAngularSpeedRadS

object DriveConfig:

  private def env(name: String): Option[String] =
    Option(System.getenv(name)).map(_.trim).filter(_.nonEmpty)

  private def envDouble(name: String, fallback: Double): Double =
    env(name).flatMap(_.toDoubleOption).getOrElse(fallback)

  private def envBoolean(name: String, fallback: Boolean): Boolean =
    env(name).map(_.toLowerCase).map(v => v == "true" || v == "1" || v == "yes").getOrElse(fallback)

  /**
   * The configuration the running system uses, read once from the environment so that the
   * platform constants and the two frame conventions can be corrected in the field with a
   * restart instead of a rebuild. Use `CalibrateDrive` to measure the conventions.
   */
  lazy val fromEnvironment: DriveConfig =
    val defaults = DriveConfig()
    DriveConfig(
      wheelBaseM = envDouble("DRIVE_WHEEL_BASE_M", defaults.wheelBaseM),
      maxLinearSpeedMs = envDouble("DRIVE_MAX_SPEED_MS", defaults.maxLinearSpeedMs),
      minDutyCycle = envDouble("DRIVE_MIN_DUTY", defaults.minDutyCycle),
      minCommand = envDouble("DRIVE_MIN_COMMAND", defaults.minCommand),
      firmwareEmaAlpha = envDouble("DRIVE_FIRMWARE_EMA_ALPHA", defaults.firmwareEmaAlpha),
      markerYawOffsetRad =
        envDouble("DRIVE_MARKER_YAW_OFFSET_DEG", math.toDegrees(defaults.markerYawOffsetRad)) * math.Pi / 180.0,
      invertWheels = envBoolean("DRIVE_INVERT_WHEELS", defaults.invertWheels)
    )

/**
 * Differential-drive kinematics and the inverse of the firmware's actuator map.
 *
 * Everything the controller reasons about is in physical units (m/s, rad/s); this object is the
 * only place that knows how those become the dimensionless `left`/`right` numbers on the wire.
 */
object DifferentialDrive:

  /** Commands below this fraction of full wheel speed are treated as "stop" rather than boosted. */
  private val negligibleWheelFraction = 0.02

  def normalizeAngle(angle: Double): Double =
    if angle.isNaN then Double.NaN
    else
      val wrapped = math.IEEEremainder(angle, 2 * math.Pi)
      if wrapped <= -math.Pi then wrapped + 2 * math.Pi
      else if wrapped > math.Pi then wrapped - 2 * math.Pi
      else wrapped

  /** The direction the robot body faces, in world coordinates, given the marker yaw from vision. */
  def bodyHeading(markerYawRad: Double, config: DriveConfig): Double =
    normalizeAngle(markerYawRad + config.markerYawOffsetRad)

  /** The robot's forward unit vector in world coordinates, given the marker yaw from vision. */
  def headingVector(markerYawRad: Double, config: DriveConfig): (Double, Double) =
    val heading = bodyHeading(markerYawRad, config)
    (math.cos(heading), math.sin(heading))

  private def clamp(value: Double, limit: Double): Double =
    math.max(-limit, math.min(limit, value))

  /**
   * Turn a body twist into the pair of wheel commands to publish.
   *
   * The turn rate has priority over the forward speed. Steering accuracy is what keeps the heading
   * loop honest, whereas arriving slightly fast or slow only changes how long the trip takes, so
   * the forward speed gives up whatever headroom the turn needs. That also means the wheels are
   * mixed to fit inside [-1, 1] by construction and never have to be clipped - clipping them
   * independently, as the firmware would, flattens the difference between them and straightens the
   * robot out exactly when it is turning hardest.
   *
   * @param linearMs    forward speed, m/s (negative drives in reverse)
   * @param angularRadS counter-clockwise turn rate, rad/s
   * @return `(left, right)` in `[-1, 1]`, ready for the wire
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

  /** True for wheel speeds we will actually ask for: stopped, or fast enough to reliably turn. */
  private def isReachable(wheelFraction: Double, config: DriveConfig): Boolean =
    wheelFraction == 0.0 || math.abs(wheelFraction) >= config.minWheelFraction

  /**
   * Nudge a wheel pair onto speeds the motors can hold.
   *
   * A wheel cannot be driven slower than `minWheelFraction`, so a pair with a wheel inside that
   * band has to be adjusted, and something has to give. The options are tried in order of how
   * little they cost:
   *
   *  1. Scale both wheels. Their ratio survives, so the robot follows the very same path - it just
   *     covers it faster than asked, which is the only way to cover it at all.
   *  2. Shift both wheels by the same amount. Their difference survives, so the turn rate is exact
   *     and the forward speed absorbs the change.
   *  3. Turn on the spot at the requested rate. Aiming is what the heading loop depends on, so it
   *     is the last thing to give up.
   */
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

  /**
   * Invert the firmware's stiction remap, so that a requested fraction of full wheel speed comes
   * out of the motor as that fraction rather than as `0.3 + 0.7 * fraction`.
   *
   * `reachablePair` has already moved the pair onto speeds of at least `minWheelFraction`, which is
   * by definition the speed `minCommand` produces, so the floor below is a guard rather than a
   * correction: it cannot round a real command up and distort the path, but it does guarantee that
   * nothing in `(0, minCommand)` ever reaches a robot.
   */
  private def toCommand(wheelFraction: Double, config: DriveConfig): Double =
    val magnitude = math.min(1.0, math.abs(wheelFraction))
    if magnitude <= 0.0 then 0.0
    else
      val command = (magnitude - config.minDutyCycle) / (1.0 - config.minDutyCycle)
      math.signum(wheelFraction) * math.max(config.minCommand, math.min(1.0, command))

  /** The duty cycle the firmware will actually drive for `command`. Used by tests and diagnostics. */
  def firmwareDutyCycle(command: Double, config: DriveConfig): Double =
    val magnitude = math.min(1.0, math.abs(command))
    if magnitude <= 0.0 then 0.0
    else config.minDutyCycle + magnitude * (1.0 - config.minDutyCycle)
