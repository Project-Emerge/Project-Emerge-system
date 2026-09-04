package it.unibo.demo.robot

/**
 * A model of one real robot, faithful enough to be worth testing the controller against.
 *
 * It reproduces the parts of the plant the controller has to cope with, all taken from the DropBot
 * firmware rather than invented here:
 *
 *  - the per-arrival EMA on incoming commands (`set_speed`), which is what makes the actuator lag
 *    depend on how often we talk to the robot;
 *  - the stiction remap onto `[minDutyCycle, 1]` with its step at zero (`duty_cycle_percent`),
 *    truncated to whole percent as the firmware's `as u8` does;
 *  - `Stop` zeroing the filter outright instead of easing into it;
 *  - differential-drive kinematics, with the pose reported in the marker frame the cameras see,
 *    so the marker-to-body conversion is exercised too.
 */
final class RobotPlant(val config: DriveConfig, var x: Double, var y: Double, var markerYaw: Double):

  private var filteredLeft = 0.0
  private var filteredRight = 0.0

  def receiveMove(left: Double, right: Double): Unit =
    filteredLeft = FirmwareLagCompensator.advance(filteredLeft, left, config.firmwareEmaAlpha)
    filteredRight = FirmwareLagCompensator.advance(filteredRight, right, config.firmwareEmaAlpha)

  def receiveStop(): Unit =
    filteredLeft = 0.0
    filteredRight = 0.0

  private def wheelSpeed(command: Double): Double =
    val duty = math.floor(DifferentialDrive.firmwareDutyCycle(command, config) * 100.0) / 100.0
    math.signum(command) * duty * config.maxLinearSpeedMs

  def integrate(dtSeconds: Double): Unit =
    val leftSpeed = wheelSpeed(filteredLeft)
    val rightSpeed = wheelSpeed(filteredRight)
    val linear = (leftSpeed + rightSpeed) / 2.0
    val angular = (rightSpeed - leftSpeed) / config.wheelBaseM
    markerYaw = DifferentialDrive.normalizeAngle(markerYaw + angular * dtSeconds)
    val heading = DifferentialDrive.bodyHeading(markerYaw, config)
    x += linear * math.cos(heading) * dtSeconds
    y += linear * math.sin(heading) * dtSeconds

  def heading: Double = DifferentialDrive.bodyHeading(markerYaw, config)
  def distanceTo(goalX: Double, goalY: Double): Double = math.hypot(goalX - x, goalY - y)

/**
 * One moment of a run, for asserting on the shape of the trajectory rather than just its end.
 *
 * `alignmentError` is measured against the robot's travel axis rather than its nose, since driving
 * in reverse is a legitimate way to reach a goal behind you. `totalRotation` accumulates every
 * degree the robot turns, which is what separates "drove there" from "spun its way there".
 */
final case class Sample(
    time: Double,
    x: Double,
    y: Double,
    distance: Double,
    alignmentError: Double,
    totalRotation: Double
)

object RobotPlant:

  /**
   * Run the whole loop the way it runs in production: the controller on one clock, the motor
   * publisher on another and faster one, and the robot integrating continuously between them.
   */
  def driveToGoal(
      start: (Double, Double, Double),
      goal: (Double, Double),
      config: DriveConfig = DriveConfig(),
      gains: ControlGains = ControlGains(),
      controlPeriod: Double = 1.0 / 20,
      publishPeriod: Double = MotorCommandPublisher.defaultPeriod.toNanos / 1e9,
      duration: Double = 20.0,
      microStep: Double = 0.002
  ): Vector[Sample] =
    val plant = RobotPlant(config, start._1, start._2, start._3)
    var state = ControlState.initial
    var desired: Option[(Double, Double)] = None
    var shadowLeft = 0.0
    var shadowRight = 0.0
    var nextControl = 0.0
    var nextPublish = 0.0
    var time = 0.0
    var totalRotation = 0.0
    var previousYaw = plant.markerYaw
    val samples = Vector.newBuilder[Sample]

    while time < duration do
      if time >= nextControl then
        nextControl += controlPeriod
        val distance = plant.distanceTo(goal._1, goal._2)
        val targetAngle = math.atan2(goal._2 - plant.y, goal._1 - plant.x)
        val (twist, next) = HeadingController.step(
          state, controlPeriod, plant.heading, targetAngle, distance,
          translate = true, allowReverse = true, config = config, gains = gains
        )
        state = next
        desired =
          if twist.isStill then None
          else
            val wheels = DifferentialDrive.toWheels(twist.linearMs, twist.angularRadS, config)
            Option.unless(wheels == (0.0, 0.0))(wheels)

      if time >= nextPublish then
        nextPublish += publishPeriod
        desired match
          case None =>
            shadowLeft = 0.0
            shadowRight = 0.0
            plant.receiveStop()
          case Some((left, right)) =>
            val alpha = config.firmwareEmaAlpha
            val outLeft = FirmwareLagCompensator.command(shadowLeft, left, alpha)
            val outRight = FirmwareLagCompensator.command(shadowRight, right, alpha)
            shadowLeft = FirmwareLagCompensator.advance(shadowLeft, outLeft, alpha)
            shadowRight = FirmwareLagCompensator.advance(shadowRight, outRight, alpha)
            plant.receiveMove(outLeft, outRight)

      plant.integrate(microStep)
      time += microStep
      totalRotation += math.abs(DifferentialDrive.normalizeAngle(plant.markerYaw - previousYaw))
      previousYaw = plant.markerYaw
      val distance = plant.distanceTo(goal._1, goal._2)
      val targetAngle = math.atan2(goal._2 - plant.y, goal._1 - plant.x)
      val forwardError = DifferentialDrive.normalizeAngle(targetAngle - plant.heading)
      val alignmentError = DifferentialDrive.normalizeAngle(
        if math.abs(forwardError) <= math.Pi / 2 then forwardError
        else forwardError - math.signum(forwardError) * math.Pi
      )
      samples += Sample(time, plant.x, plant.y, distance, alignmentError, totalRotation)

    samples.result()
