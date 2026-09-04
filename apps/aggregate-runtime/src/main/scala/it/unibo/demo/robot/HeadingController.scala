package it.unibo.demo.robot

/**
 * Tuning for [[HeadingController]]. Unlike [[DriveConfig]], which describes the hardware, these
 * are genuine control knobs.
 *
 * @param spinGain            rad/s of turn rate per rad of heading error, while turning on the
 *                            spot. This is the one to raise to make the robots turn faster
 * @param steerGain           the same, but while driving. Deliberately gentler: once the robot is
 *                            rolling, its heading and its position feed each other - the bearing to
 *                            the goal moves as the robot does - and a gain that is crisp for a
 *                            standing turn makes that loop weave across the path instead of
 *                            settling onto it
 * @param derivativeGain      rad/s of turn rate per rad/s of heading-error rate
 * @param derivativeCutoffHz  low-pass corner for the derivative term, to keep vision noise out of it
 * @param alignToleranceRad   heading error below which the robot is considered aimed
 * @param driveResumeRad      start translating once the heading error drops below this
 * @param driveAbortRad       stop translating and turn in place once the error grows past this
 * @param reverseMarginRad    how much better the reverse plan must be before switching to it
 * @param slowdownRadiusM     distance over which the approach speed ramps down to the goal
 * @param distanceToleranceM  distance below which the goal counts as reached
 * @param angularAccelRadS2   turn-rate slew limit, also the deceleration used to arrive on heading
 * @param linearAccelMs2      linear-speed slew limit
 * @param turnRateBudget      how far the robot may turn in a single control period, counted in
 *                            alignment tolerances. Raising it makes the robot spin faster; raising
 *                            it too far reintroduces hunting, because the heading can then jump
 *                            across the target between two updates faster than the controller can
 *                            react. See [[HeadingController.angularCommand]]
 */
final case class ControlGains(
    spinGain: Double = 5.0,
    steerGain: Double = 2.2,
    derivativeGain: Double = 0.35,
    derivativeCutoffHz: Double = 3.0,
    alignToleranceRad: Double = 10.0 * math.Pi / 180.0,
    driveResumeRad: Double = 35.0 * math.Pi / 180.0,
    driveAbortRad: Double = 60.0 * math.Pi / 180.0,
    reverseMarginRad: Double = 30.0 * math.Pi / 180.0,
    slowdownRadiusM: Double = 0.20,
    distanceToleranceM: Double = 0.03,
    angularAccelRadS2: Double = 18.0,
    linearAccelMs2: Double = 0.7,
    turnRateBudget: Double = 1.5
)

/**
 * Per-robot controller memory. Immutable, so the controller stays a pure function and can be
 * exercised in a closed loop by the tests.
 *
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

/**
 * A differential-drive controller that aims first and drives second.
 *
 * The two properties that make it stable where the previous one was not:
 *
 *  1. It commands a physical turn rate, and caps that rate at what the current control period can
 *     absorb (`alignToleranceRad / dt`). The heading can therefore never cross the tolerance band
 *     inside a single tick, which is what made the old controller overshoot on every update.
 *  2. It slew-limits its outputs instead of low-pass filtering them. A filter on the output of a
 *     feedback loop buys smoothness with phase lag; a slew limit bounds the rate of change without
 *     adding any.
 *
 * Translation is gated on being roughly aimed, with hysteresis, so the robot drives in straight
 * lines rather than arcs of a radius set by an unbounded correction term.
 */
object HeadingController:

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
      // Swapping between the forward and reverse plans moves the error by half a turn in one tick.
      // That is a change of reference frame, not a change in the robot, so the derivative term must
      // not read it as one - it would answer a stationary robot with a violent correction.
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

  /**
   * Pick between driving forwards and backwards towards the goal, and stick with the choice.
   *
   * Without the margin the two plans are equally good at a heading error of 90 degrees, so the
   * controller flips between them every tick and never commits to either.
   */
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

  /**
   * The coarsest the robot can aim while standing still, at this update rate.
   *
   * Turning on the spot means driving the wheels in opposite directions, and the motors stall below
   * a minimum duty cycle, so there is a slowest spin the platform can hold - one tick of which
   * covers `minAngularSpeedRadS * dt`. Asking to be aimed more precisely than that asks for
   * something the hardware cannot do: the robot steps straight across the target every tick and
   * hunts forever. Accepting a coarser aim trades accuracy, which degrades gracefully, for
   * convergence, which does not.
   */
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
    // A robot that is already rolling can steer as gently as it likes: a small difference on top of
    // a large common wheel speed is well clear of the stall band, so there is no minimum turn rate
    // to work around and no reason for a dead zone. Standing still, there is, so aiming stops once
    // it is as close as a single tick of the slowest possible spin can get it.
    if !driving && math.abs(error) < floor then (derivative, 0.0)
    else if driving && math.abs(error) < 1e-4 then (derivative, 0.0)
    else
      // Never ask for more turn than this period can absorb, and decelerate into the target so the
      // last tick before alignment does not fly past it. The deceleration is what protects the
      // endgame; the period cap is what stops a fast loop from being outrun by a fast platform.
      val periodLimit = gains.turnRateBudget * floor / dtSeconds
      val approachLimit = math.sqrt(2.0 * gains.angularAccelRadS2 * math.abs(error))
      val limit = math.min(config.maxAngularSpeedRadS, math.min(periodLimit, approachLimit))
      val gain = if driving then gains.steerGain else gains.spinGain
      val desired = gain * error + gains.derivativeGain * derivative
      (derivative, math.max(-limit, math.min(limit, desired)))

  /** Hysteresis on the "aimed well enough to drive" latch, so it cannot chatter. */
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
