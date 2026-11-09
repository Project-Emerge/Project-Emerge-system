package it.unibo.demo.robot

class DifferentialDriveSuite extends munit.FunSuite:

  private val config = DriveConfig()
  private val tolerance = 1e-9

  /**
   * The firmware truncates its duty cycle to whole percent (`as u8`), so roughly 1% of full wheel
   * speed is the finest distinction the hardware can make. Asserting tighter than that would be
   * asserting about arithmetic rather than about the robot.
   */
  private def assertWithinOnePercent(actual: Double, expected: Double, clue: String): Unit =
    assertEqualsDouble(actual, expected, math.abs(expected) * 0.01 + 1e-9, clue)

  /** Undo everything `toWheels` did, by running the commands through the firmware's own map. */
  private def executedTwist(left: Double, right: Double, config: DriveConfig = config): (Double, Double) =
    val (physicalLeft, physicalRight) = if config.invertWheels then (right, left) else (left, right)
    def wheelSpeed(command: Double): Double =
      math.signum(command) * DifferentialDrive.firmwareDutyCycle(command, config) * config.maxLinearSpeedMs
    val vl = wheelSpeed(physicalLeft)
    val vr = wheelSpeed(physicalRight)
    ((vl + vr) / 2.0, (vr - vl) / config.wheelBaseM)

  test("normalizeAngle maps onto (-Pi, Pi]"):
    assertEqualsDouble(DifferentialDrive.normalizeAngle(0.0), 0.0, tolerance)
    assertEqualsDouble(DifferentialDrive.normalizeAngle(3 * math.Pi), math.Pi, tolerance)
    assertEqualsDouble(DifferentialDrive.normalizeAngle(-math.Pi), math.Pi, tolerance)
    assertEqualsDouble(DifferentialDrive.normalizeAngle(1.5 * math.Pi), -0.5 * math.Pi, tolerance)
    assertEqualsDouble(DifferentialDrive.normalizeAngle(-1.5 * math.Pi), 0.5 * math.Pi, tolerance)

  test("bodyHeading rotates the marker yaw onto the robot's forward axis"):
    val (x, y) = DifferentialDrive.headingVector(0.0, config)
    // The default offset says forward is the marker's +Y axis, so a yaw of zero points at world +Y.
    assertEqualsDouble(x, 0.0, 1e-12)
    assertEqualsDouble(y, 1.0, 1e-12)
    assertEqualsDouble(DifferentialDrive.bodyHeading(-math.Pi / 2, config), 0.0, tolerance)

  test("a twist the platform can hold comes back out of the firmware map unchanged"):
    // The point of inverting the stiction remap: what the controller asked for is what the motors
    // do. Without the inversion these would all come back scaled by 0.3 + 0.7 * command.
    for
      linear <- List(0.2, 0.25, 0.3)
      angular <- List(-0.6, -0.3, 0.0, 0.3, 0.6)
    do
      val (left, right) = DifferentialDrive.toWheels(linear, angular, config)
      val (executedLinear, executedAngular) = executedTwist(left, right)
      assertWithinOnePercent(executedLinear, linear, s"linear for ($linear, $angular)")
      assertWithinOnePercent(executedAngular, angular, s"angular for ($linear, $angular)")

  test("no twist inside the platform's limits ever under-delivers its turn rate"):
    // Aiming is what the heading loop depends on, so the mixer gives up speed before turn rate.
    // Whatever adjustment a twist needs, the robot turns at least as hard as it was asked to and
    // always in the requested direction - never the old behaviour of quietly swallowing the turn.
    for
      linear <- List(-0.35, -0.1, 0.0, 0.05, 0.12, 0.35)
      angular <- List(-6.0, -1.5, -0.2, 0.2, 1.5, 6.0)
    do
      val (left, right) = DifferentialDrive.toWheels(linear, angular, config)
      val (_, executedAngular) = executedTwist(left, right)
      val clue = s"angular for ($linear, $angular) was $executedAngular"
      assertEquals(math.signum(executedAngular), math.signum(angular), clue)
      assert(math.abs(executedAngular) >= math.abs(angular) * 0.99, clue)

  test("a twist that only needs speeding up keeps the shape of its path"):
    // Step one of the ladder: scaling both wheels leaves their ratio, and so the arc, intact.
    val (linear, angular) = (-0.1, -0.2)
    val (left, right) = DifferentialDrive.toWheels(linear, angular, config)
    val (executedLinear, executedAngular) = executedTwist(left, right)
    assertWithinOnePercent(executedAngular / executedLinear, angular / linear, "same arc")

  test("a twist that cannot be scaled keeps its turn rate exactly"):
    // Step two of the ladder: shifting both wheels leaves their difference, and so the turn, intact.
    val (linear, angular) = (0.12, -1.5)
    val (left, right) = DifferentialDrive.toWheels(linear, angular, config)
    val (executedLinear, executedAngular) = executedTwist(left, right)
    assertWithinOnePercent(executedAngular, angular, "the turn rate is what has to be right")
    assert(executedLinear != linear, "the forward speed is what absorbs the adjustment")

  test("driving straight drives straight"):
    val (left, right) = DifferentialDrive.toWheels(0.25, 0.0, config)
    assertEqualsDouble(left, right, tolerance)
    val (_, executedAngular) = executedTwist(left, right)
    assertEqualsDouble(executedAngular, 0.0, tolerance)

  test("an in-place spin is symmetric"):
    val (left, right) = DifferentialDrive.toWheels(0.0, 2.0, config)
    assertEqualsDouble(left, -right, tolerance)
    assert(right > 0, "a counter-clockwise turn must drive the right wheel forward")

  test("an oversized twist gives up speed, not turn rate"):
    val (left, right) = DifferentialDrive.toWheels(0.3, 6.0, config)
    assert(math.abs(left) <= 1.0 && math.abs(right) <= 1.0)
    val (executedLinear, executedAngular) = executedTwist(left, right)
    assertWithinOnePercent(executedAngular, 6.0, "the turn rate is what has to be right")
    assert(executedLinear < 0.3, "the forward speed is what gives up the headroom")
    assert(executedLinear > 0.0, "but the robot should still make progress")

  test("a twist too slow to hold keeps the shape of the path"):
    // The old controller zeroed anything under a 0.10 deadband, so fine corrections did nothing.
    // The motors cannot creep, so the only honest answer is to drive the same arc faster.
    val (linear, angular) = (0.02, 0.15)
    val (left, right) = DifferentialDrive.toWheels(linear, angular, config)
    assert(left != 0.0 || right != 0.0, "a small but real twist must still produce motion")
    val (executedLinear, executedAngular) = executedTwist(left, right)
    assertWithinOnePercent(executedAngular / executedLinear, angular / linear, "same path, faster")
    assert(executedLinear > linear, "the platform cannot creep this slowly")

  test("no command is ever small enough to leave a robot humming instead of moving"):
    // A command just above zero is meant to clear the firmware's stiction floor, but that floor is
    // one fleet-wide constant and the real robots do not all start at it - near-zero commands were
    // observed to leave them buzzing in place. Every command is either a clean zero or big enough
    // to be well clear of wherever the true threshold happens to sit.
    val commands = for
      linear <- (-40 to 40).map(_ * 0.01)
      angular <- (-40 to 40).map(_ * 0.25)
      command <- DifferentialDrive.toWheels(linear, angular, config).toList
    yield command
    val stalled = commands.filter(command => command != 0.0 && math.abs(command) < config.minCommand)
    assert(stalled.isEmpty, s"${stalled.size} commands landed under ${config.minCommand}: ${stalled.take(5)}")

  test("the floor still holds when the firmware needs no stiction help at all"):
    // min_duty_cycle 0 is the case where the firmware does nothing for us, so the whole low end of
    // the range is ours to keep clear.
    val plain = config.copy(minDutyCycle = 0.0)
    val commands = for
      linear <- (-20 to 20).map(_ * 0.02)
      angular <- (-10 to 10).map(_ * 0.5)
      command <- DifferentialDrive.toWheels(linear, angular, plain).toList
    yield command
    val stalled = commands.filter(command => command != 0.0 && math.abs(command) < plain.minCommand)
    assert(stalled.isEmpty, s"${stalled.size} commands landed under ${plain.minCommand}: ${stalled.take(5)}")

  test("the slowest command the mixer emits is exactly the floor, not above it"):
    // If the floor were enforced by rounding up rather than by construction, the slowest usable
    // speed would be unreachable and the robot would always overshoot it.
    val (left, right) = DifferentialDrive.toWheels(config.minLinearSpeedMs, 0.0, config)
    assertEqualsDouble(left, config.minCommand, 1e-9)
    assertEqualsDouble(right, config.minCommand, 1e-9)

  test("a negligible twist stops the motors instead of lurching at the stall duty"):
    assertEquals(DifferentialDrive.toWheels(0.0, 0.0, config), (0.0, 0.0))
    assertEquals(DifferentialDrive.toWheels(1e-6, 1e-6, config), (0.0, 0.0))

  test("NaN input stops the motors"):
    assertEquals(DifferentialDrive.toWheels(Double.NaN, 0.3, config), (0.0, 0.0))
    assertEquals(DifferentialDrive.toWheels(0.3, Double.NaN, config), (0.0, 0.0))

  test("invertWheels swaps the published pair"):
    val inverted = config.copy(invertWheels = true)
    val (left, right) = DifferentialDrive.toWheels(0.2, 1.0, config)
    val (invertedLeft, invertedRight) = DifferentialDrive.toWheels(0.2, 1.0, inverted)
    assertEquals((invertedLeft, invertedRight), (right, left))

  test("commands never leave the range the firmware accepts"):
    for
      linear <- List(-1.0, -0.35, 0.0, 0.35, 1.0)
      angular <- List(-20.0, -7.0, 0.0, 7.0, 20.0)
    do
      val (left, right) = DifferentialDrive.toWheels(linear, angular, config)
      assert(math.abs(left) <= 1.0 && math.abs(right) <= 1.0, s"($linear, $angular) -> ($left, $right)")

  test("the lag compensator lands the firmware filter on target and holds it"):
    val alpha = 0.1
    var shadow = 0.0
    val target = 0.4
    val published = (1 to 10).map { _ =>
      val command = FirmwareLagCompensator.command(shadow, target, alpha)
      shadow = FirmwareLagCompensator.advance(shadow, command, alpha)
      shadow
    }
    assert(math.abs(published(4) - target) < 1e-9, s"expected convergence within 5 messages, got $published")
    published.drop(5).foreach(value => assertEqualsDouble(value, target, 1e-9))
    // Without compensation the same 10 messages only get 65% of the way there.
    assert(1.0 - math.pow(1 - alpha, 10) < 0.66)

  test("the lag compensator is a no-op when the firmware does not filter"):
    assertEqualsDouble(FirmwareLagCompensator.command(0.0, 0.4, 1.0), 0.4, tolerance)
