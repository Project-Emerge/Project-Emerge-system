package it.unibo.demo.robot

class HeadingControllerSuite extends munit.FunSuite:

  private val config = DriveConfig()
  private val gains = ControlGains()

  private def settled(samples: Vector[Sample]): Sample = samples.last

  test("the robot reaches its goal from every starting heading"):
    // The failure this replaces was direction-dependent: the robot orbited instead of arriving, and
    // whether it converged at all depended on where it happened to be pointing.
    val failures = for
      degrees <- (0 until 360 by 10).toList
      distance <- List(0.15, 0.5, 1.5)
      samples = RobotPlant.driveToGoal(
        start = (0.0, 0.0, math.toRadians(degrees.toDouble)),
        goal = (distance, 0.0),
        config = config,
        gains = gains
      )
      если = settled(samples)
      if если.distance > 0.05
    yield s"start ${degrees}deg, goal ${distance}m: stopped ${если.distance}m short"
    assert(failures.isEmpty, failures.mkString("\n"))

  test("the robot arrives and stays put"):
    val samples = RobotPlant.driveToGoal((0.0, 0.0, 0.0), (0.8, 0.4))
    val lastFiveSeconds = samples.filter(_.time > samples.last.time - 5.0)
    val drift = lastFiveSeconds.map(_.distance).max - lastFiveSeconds.map(_.distance).min
    assert(settled(samples).distance <= 0.05, s"stopped ${settled(samples).distance}m short")
    assert(drift < 0.01, s"the robot kept fidgeting at the goal: ${drift}m of drift")

  test("the heading error does not run away once the robot is driving"):
    // The old controller's symptom was the error growing rather than shrinking: it commanded a turn
    // far larger than one control period could absorb, overshot, and swung back further every time.
    val samples = RobotPlant.driveToGoal((0.0, 0.0, math.toRadians(150.0)), (1.0, 0.0))
    val approach = samples.filter(sample => sample.distance > 0.1 && sample.time > 1.5)
    val worst = approach.map(sample => math.abs(sample.alignmentError)).maxOption.getOrElse(0.0)
    assert(worst < math.toRadians(45.0), s"heading error reached ${math.toDegrees(worst)}deg while approaching")

  test("the robot drives in a straight line once it is aimed"):
    // The complaint this fixes, stated as a test: when the robot needs to go straight, it goes
    // straight, instead of arcing at whatever radius the correction term happened to produce.
    val goal = (1.2, 0.0)
    // A marker yaw of zero points the body at world +Y, square to the goal, so the robot has to
    // turn a quarter turn before it can make any progress.
    val samples = RobotPlant.driveToGoal((0.0, 0.0, 0.0), goal)
    val aimed = samples.find(sample => math.abs(sample.alignmentError) < math.toRadians(10.0))
    assert(aimed.isDefined, "the robot never lined up with its goal")
    val origin = aimed.get
    val run = math.hypot(goal._1 - origin.x, goal._2 - origin.y)
    def offLine(sample: Sample): Double =
      math.abs((goal._1 - origin.x) * (sample.y - origin.y) - (goal._2 - origin.y) * (sample.x - origin.x)) / run
    val after = samples.dropWhile(_.time < origin.time)
    val offsets = after.map(offLine)

    // The robot is still finishing its turn as it crosses the alignment threshold, so the path
    // bulges a little before settling. What matters is that it settles: the bulge is small, it
    // happens once, and the path closes back onto the line instead of weaving across it.
    assert(offsets.max < 0.05, s"wandered ${offsets.max}m off the straight line to the goal")
    assert(offsets.last < offsets.max / 2, s"never settled back onto the line: ended ${offsets.last}m off")
    val crossings = after.map(sample => math.signum(sample.alignmentError)).sliding(2)
      .count { case Seq(before, next) => before != 0.0 && next != 0.0 && before != next }
    assertEquals(crossings, 0, "the heading error should decay, not oscillate through zero")

  test("the robot does not take the scenic route"):
    val goal = (1.2, 0.0)
    val samples = RobotPlant.driveToGoal((0.0, 0.0, 0.0), goal)
    val travelled = samples.sliding(2).map { case Seq(a, b) => math.hypot(b.x - a.x, b.y - a.y) }.sum
    val direct = math.hypot(goal._1, goal._2)
    assert(travelled < direct * 1.25, s"travelled ${travelled}m to cover ${direct}m")

  test("it still converges at the old 5 Hz control rate"):
    // The regression test for the bug this replaces. At 200 ms per command the firmware's own
    // smoothing has a two-second time constant and the platform turns tens of degrees per tick, so
    // this is the case the previous controller could not survive.
    val samples = RobotPlant.driveToGoal(
      start = (0.0, 0.0, math.toRadians(170.0)),
      goal = (1.0, 0.0),
      controlPeriod = 0.2,
      publishPeriod = 0.2,
      duration = 30.0
    )
    assert(settled(samples).distance <= 0.08, s"stopped ${settled(samples).distance}m short at 5 Hz")

  test("it converges even without the lag compensation"):
    // Setting the alpha to 1 models a firmware with its EMA disabled, and also stands in for the
    // compensator's shadow being wrong. Neither should be load-bearing for stability.
    val samples = RobotPlant.driveToGoal(
      start = (0.0, 0.0, math.toRadians(200.0)),
      goal = (0.9, 0.3),
      config = config.copy(firmwareEmaAlpha = 1.0)
    )
    assert(settled(samples).distance <= 0.05, s"stopped ${settled(samples).distance}m short")

  test("a wrong wheel convention makes the robot spin its way to the goal"):
    // Documents the failure mode this whole change is about, and pins down what it looks like: with
    // left and right swapped the steering feedback is positive, so every correction turns the robot
    // away from where it wants to go and it only ever lines up by coming all the way around. It may
    // still arrive, which is exactly why arrival alone is a poor thing to measure - the tell is how
    // much the robot turned on the way. `CalibrateDrive` exists so this is never a guess.
    // A marker yaw of -50 degrees points the body 40 degrees off the goal: inside the align gate,
    // so a healthy robot turns 40 degrees once and then drives.
    val start = (0.0, 0.0, math.toRadians(-50.0))
    val goal = (1.0, 0.0)
    val correct = RobotPlant.driveToGoal(start, goal, config = config)
    val swapped = RobotPlant.driveToGoal(start, goal, config = config.copy(invertWheels = true))
    val turnedCorrectly = math.toDegrees(settled(correct).totalRotation)
    val turnedSwapped = math.toDegrees(settled(swapped).totalRotation)
    assert(turnedCorrectly < 180.0, s"the right convention should turn once, not $turnedCorrectly deg")
    assert(turnedSwapped > 4 * turnedCorrectly,
      s"a reversed steering sign should be obvious: $turnedSwapped deg vs $turnedCorrectly deg")

  test("the alignment tolerance widens when the loop is too slow to hold a tighter one"):
    val slow = 0.2
    val fast = 0.02
    // The platform cannot turn slower than minAngularSpeedRadS, so one tick covers that much angle;
    // asking for better than that at a given rate is asking the hardware for the impossible.
    assert(config.minAngularSpeedRadS * slow > gains.alignToleranceRad)
    assert(config.minAngularSpeedRadS * fast < gains.alignToleranceRad)

    def restingError(dt: Double): Double =
      val samples = RobotPlant.driveToGoal(
        (0.0, 0.0, math.toRadians(90.0)), (0.6, 0.0),
        controlPeriod = dt, publishPeriod = dt, duration = 30.0
      )
      math.abs(samples.last.alignmentError)

    // Both settle; the slower loop simply admits it cannot aim as precisely, instead of hunting.
    val fine = restingError(fast)
    val coarse = restingError(slow)
    assert(fine < math.toRadians(20.0), s"at ${fast}s per tick the robot rested ${math.toDegrees(fine)}deg off")
    assert(coarse < math.toRadians(40.0), s"at ${slow}s per tick the robot rested ${math.toDegrees(coarse)}deg off")

  test("the forward and reverse plans do not chatter"):
    // A goal square abeam is the worst case: forwards and backwards are equally good, and the old
    // controller flipped between them every tick, resetting its own state each time.
    var state = ControlState.initial
    val flips = (1 to 200).count { tick =>
      val wasReversing = state.reversing
      // Heading error parked at 90 degrees, jittering by a degree the way vision noise would.
      val heading = math.toRadians(if tick % 2 == 0 then 0.5 else -0.5)
      val (_, next) = HeadingController.step(
        state, 0.05, heading, math.Pi / 2, 0.5,
        translate = true, allowReverse = true, config = config, gains = gains
      )
      state = next
      state.reversing != wasReversing
    }
    assertEquals(flips, 0, "the forward/reverse choice must be sticky")

  test("a stopped robot is commanded to stop, not to creep"):
    val (twist, state) = HeadingController.step(
      ControlState.initial, 0.05, 0.0, 0.0, 0.01,
      translate = true, allowReverse = true, config = config, gains = gains
    )
    assert(twist.isStill, s"expected a full stop within the distance tolerance, got $twist")
    assertEquals(state, ControlState.initial, "arriving must clear the controller's history")

  test("a zero or negative time step is ignored rather than dividing by it"):
    for dt <- List(0.0, -0.05, Double.NaN) do
      val (twist, state) = HeadingController.step(
        ControlState.initial, dt, 0.0, math.Pi, 1.0,
        translate = true, allowReverse = true, config = config, gains = gains
      )
      assert(twist.isStill)
      assertEquals(state, ControlState.initial)

  test("switching between driving forwards and backwards does not jolt the steering"):
    // The two plans are half a turn apart, so a naive derivative reads the switch as the robot
    // having spun 180 degrees in one tick and answers with a correction it does not need.
    val settled = ControlState(previousError = Some(0.05), filteredDerivative = 0.0, reversing = false)
    val (twist, next) = HeadingController.step(
      settled, 0.05, 0.0, math.Pi, 0.5,
      translate = true, allowReverse = true, config = config, gains = gains
    )
    assert(next.reversing, "a goal directly behind should be reached in reverse")
    assert(math.abs(twist.angularRadS) < 0.2,
      s"the change of plan should not command a turn on its own, got ${twist.angularRadS} rad/s")

  /** Turn on the spot and report when the heading first lands inside the tolerance, plus overshoot. */
  private def spin(degrees: Double, gains: ControlGains = gains, dt: Double = 0.05): (Double, Double) =
    val plant = RobotPlant(config, 0.0, 0.0, 0.0)
    val target = DifferentialDrive.normalizeAngle(plant.heading + math.toRadians(degrees))
    var state = ControlState.initial
    var shadow = (0.0, 0.0)
    var elapsed = 0.0
    var settledAt = Double.NaN
    var overshoot = 0.0
    val approaching = math.signum(DifferentialDrive.normalizeAngle(target - plant.heading))
    while elapsed < 15.0 do
      val (twist, next) = HeadingController.step(
        state, dt, plant.heading, target, 0.0,
        translate = false, allowReverse = false, config = config, gains = gains)
      state = next
      val (left, right) = DifferentialDrive.toWheels(twist.linearMs, twist.angularRadS, config)
      if left == 0.0 && right == 0.0 then plant.receiveStop()
      else
        val alpha = config.firmwareEmaAlpha
        val outLeft = FirmwareLagCompensator.command(shadow._1, left, alpha)
        val outRight = FirmwareLagCompensator.command(shadow._2, right, alpha)
        shadow = (FirmwareLagCompensator.advance(shadow._1, outLeft, alpha),
                  FirmwareLagCompensator.advance(shadow._2, outRight, alpha))
        plant.receiveMove(outLeft, outRight)
      var step = 0
      while step < (dt / 0.002).toInt do { plant.integrate(0.002); step += 1 }
      elapsed += dt
      val error = DifferentialDrive.normalizeAngle(target - plant.heading)
      if math.signum(error) != approaching then overshoot = math.max(overshoot, math.toDegrees(math.abs(error)))
      if settledAt.isNaN && math.abs(error) < math.toRadians(10.0) then settledAt = elapsed
    (settledAt, overshoot)

  test("turning on the spot is quick"):
    // Pins the turn rate so it cannot quietly drift back down. The platform can manage about
    // 400 deg/s; these leave room for the ramp up and the deceleration onto the target.
    val (ninety, _) = spin(90.0)
    val (oneEighty, _) = spin(180.0)
    assert(ninety <= 0.6, f"a quarter turn took ${ninety}%.2fs (${90.0 / ninety}%.0f deg/s)")
    assert(oneEighty <= 1.0, f"a half turn took ${oneEighty}%.2fs (${180.0 / oneEighty}%.0f deg/s)")

  test("turning quickly does not mean overshooting"):
    // Speed is only worth having if the robot still stops where it was aimed. The old controller
    // was fast in exactly this sense and swung past the target every time.
    for degrees <- List(30.0, 90.0, 150.0, 180.0, -45.0, -120.0) do
      val (settled, overshoot) = spin(degrees)
      assert(!settled.isNaN, s"a ${degrees}deg turn never settled")
      assert(overshoot < 5.0, f"a ${degrees}deg turn overshot by ${overshoot}%.1f deg")

  test("steering while driving stays gentler than turning on the spot"):
    // While rolling, heading and position feed each other, so the same gain that is crisp standing
    // still makes the robot weave. The split is deliberate and worth keeping.
    assert(gains.steerGain < gains.spinGain,
      "the driving gain must stay below the spin gain, or the approach to a goal oscillates")
