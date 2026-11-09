package it.unibo.demo.robot

/**
 * Tuning for [[HeadingController]]. Unlike [[DriveConfig]], which describes the hardware, these
 * are genuine control knobs.
 *
 * @param spinGain           turn-rate gain while stationary; increase to turn faster
 * @param steerGain          gentler turn-rate gain while driving, to avoid weaving
 * @param derivativeGain     turn-rate gain for heading-error rate
 * @param derivativeCutoffHz low-pass cutoff for the derivative term
 * @param alignToleranceRad  error below which the robot is aimed
 * @param driveResumeRad     error below which translation starts
 * @param driveAbortRad      error above which translation stops
 * @param reverseMarginRad   required advantage before switching to reverse
 * @param slowdownRadiusM    distance over which approach speed decreases
 * @param distanceToleranceM distance below which the goal is reached
 * @param angularAccelRadS2  turn-rate slew limit and heading-arrival deceleration
 * @param linearAccelMs2     linear-speed slew limit
 * @param turnRateBudget     maximum turn per period in alignment tolerances; higher values turn
 *                           faster but can reintroduce hunting. See [[HeadingController.angularCommand]]
 */
final case class ControlGains(
    spinGain: Double = 5.0,
    steerGain: Double = 2.2,
    derivativeGain: Double = 0.35,
    derivativeCutoffHz: Double = 3.0,
    alignToleranceRad: Double = math.toRadians(10.0),
    driveResumeRad: Double = math.toRadians(35.0),
    driveAbortRad: Double = math.toRadians(60.0),
    reverseMarginRad: Double = math.toRadians(30.0),
    slowdownRadiusM: Double = 0.20,
    distanceToleranceM: Double = 0.03,
    angularAccelRadS2: Double = 18.0,
    linearAccelMs2: Double = 0.7,
    turnRateBudget: Double = 1.5
)

/**
 * @param previousError      last heading error, for the derivative term
 * @param filteredDerivative low-passed heading-error rate, rad/s
 * @param linearMs           last commanded forward speed, for slew limiting
 * @param angularRadS        last commanded turn rate, for slew limiting
 * @param driving            align-gate latch: is the robot currently allowed to translate?
 * @param reversing          sticky forward/reverse choice
 */
final case class ControlState(
    previousError: Option[Double] = None,
    filteredDerivative: Double = 0.0,
    linearMs: Double = 0.0,
    angularRadS: Double = 0.0,
    driving: Boolean = false,
    reversing: Boolean = false
)

object ControlState:
  val initial: ControlState = ControlState()

/** The twist to execute, in body units. */
final case class Twist(linearMs: Double, angularRadS: Double):
  def isStill: Boolean = linearMs == 0.0 && angularRadS == 0.0

object Twist:
  val still: Twist = Twist(0.0, 0.0)

/** Differential-drive controller that aims first, then drives with slew-limited outputs. */
object HeadingController:

  /** Ignore steering commands too small for the wheels to resolve. */
  private val steerDeadbandRad = 1e-4

  /**
   * Advance the controller by one tick.
   *
   * @param state         this robot's controller memory
   * @param dtSeconds     elapsed time since the previous tick, measured rather than assumed
   * @param heading       where the robot body currently points, world frame, rad
   * @param targetAngle   the direction it should travel or face, world frame, rad
   * @param distanceToGoal metres left to travel; ignored when `translate` is false
   * @param translate     true to drive to a goal, false to only turn on the spot
   * @param allowReverse  true if backing up is an acceptable way to reach the goal
   */
  def step(
      state: ControlState,
      dtSeconds: Double,
      heading: Double,
      targetAngle: Double,
      distanceToGoal: Double,
      translate: Boolean,
      allowReverse: Boolean,
      config: DriveConfig,
      gains: ControlGains
  ): (Twist, ControlState) =
    if dtSeconds <= 0 || dtSeconds.isNaN || heading.isNaN || targetAngle.isNaN then (Twist.still, state)
    else if translate && (distanceToGoal.isNaN || distanceToGoal < gains.distanceToleranceM) then
      // Arrived. Come to rest and forget the history, so noise around the goal cannot restart a turn.
      (Twist.still, ControlState.initial)
    else
      val (error, reversing) = chooseDirection(state, heading, targetAngle, translate, allowReverse, gains)
      // A direction switch changes the reference frame by half a turn; don't feed it to the derivative.
      val history = if reversing == state.reversing then state else state.copy(previousError = None)
      val driving = translate && drivingAllowed(state.driving, error, gains)
      val (derivative, angular) = angularCommand(history, dtSeconds, error, driving, config, gains)
      val linear = linearCommand(error, distanceToGoal, driving, reversing, config, gains)

      val nextLinear = slew(state.linearMs, linear, gains.linearAccelMs2 * dtSeconds)
      val nextAngular = slew(state.angularRadS, angular, gains.angularAccelRadS2 * dtSeconds)

      val next = ControlState(
        previousError = Some(error),
        filteredDerivative = derivative,
        linearMs = nextLinear,
        angularRadS = nextAngular,
        driving = driving,
        reversing = reversing
      )
      (Twist(nextLinear, nextAngular), next)

  /** Pick the forward/reverse direction, keeping it stable near 90 degrees. */
  private def chooseDirection(
      state: ControlState,
      heading: Double,
      targetAngle: Double,
      translate: Boolean,
      allowReverse: Boolean,
      gains: ControlGains
  ): (Double, Boolean) =
    val forwardError = DifferentialDrive.normalizeAngle(targetAngle - heading)
    if !translate || !allowReverse then (forwardError, false)
    else
      val reverseError = DifferentialDrive.normalizeAngle(targetAngle + math.Pi - heading)
      val reversing =
        if state.reversing then math.abs(forwardError) + gains.reverseMarginRad > math.abs(reverseError)
        else math.abs(reverseError) + gains.reverseMarginRad < math.abs(forwardError)
      (if reversing then reverseError else forwardError, reversing)

  /** Minimum achievable aiming tolerance for one update period. */
  private def spinFloor(dtSeconds: Double, config: DriveConfig, gains: ControlGains): Double =
    math.max(gains.alignToleranceRad, config.minAngularSpeedRadS * dtSeconds)

  private def angularCommand(
      state: ControlState,
      dtSeconds: Double,
      error: Double,
      driving: Boolean,
      config: DriveConfig,
      gains: ControlGains
  ): (Double, Double) =
    val rawDerivative = state.previousError match
      case Some(previous) => DifferentialDrive.normalizeAngle(error - previous) / dtSeconds
      case None => 0.0
    val timeConstant = 1.0 / (2 * math.Pi * gains.derivativeCutoffHz)
    val blend = dtSeconds / (dtSeconds + timeConstant)
    val derivative = state.filteredDerivative + blend * (rawDerivative - state.filteredDerivative)

    val floor = spinFloor(dtSeconds, config, gains)
    // Rolling robots need no minimum turn rate; stationary robots stop within one minimum-spin tick.
    if !driving && math.abs(error) < floor then (derivative, 0.0)
    else if driving && math.abs(error) < steerDeadbandRad then (derivative, 0.0)
    else
      // Limit turn rate by the period and decelerate into the target.
      val periodLimit = gains.turnRateBudget * floor / dtSeconds
      val approachLimit = math.sqrt(2.0 * gains.angularAccelRadS2 * math.abs(error))
      val limit = math.min(config.maxAngularSpeedRadS, math.min(periodLimit, approachLimit))
      val gain = if driving then gains.steerGain else gains.spinGain
      val desired = gain * error + gains.derivativeGain * derivative
      (derivative, math.max(-limit, math.min(limit, desired)))

  /**
   * Update the translation gate with hysteresis: once driving, tolerate a larger error before
   * stopping than the error required to start. This prevents small heading fluctuations from
   * repeatedly enabling and disabling translation.
   */
  private def drivingAllowed(wasDriving: Boolean, error: Double, gains: ControlGains): Boolean =
    if wasDriving then math.abs(error) < gains.driveAbortRad
    else math.abs(error) < gains.driveResumeRad

  private def linearCommand(
      error: Double,
      distanceToGoal: Double,
      driving: Boolean,
      reversing: Boolean,
      config: DriveConfig,
      gains: ControlGains
  ): Double =
    if !driving then 0.0
    else
      val ramp = math.max(0.0, math.min(1.0, distanceToGoal / gains.slowdownRadiusM))
      val aimed = math.max(0.0, math.cos(error))
      val speed = config.maxLinearSpeedMs * ramp * aimed
      // Anything slower than this stalls the motors, so there is no point asking for it.
      val achievable = if speed <= 0.0 then 0.0 else math.max(config.minLinearSpeedMs, speed)
      if reversing then -achievable else achievable

  private def slew(current: Double, target: Double, maxChange: Double): Double =
    val delta = target - current
    if math.abs(delta) <= maxChange then target
    else current + math.signum(delta) * maxChange
