package it.unibo.demo.robot

import cats.effect.IO
import it.unibo.core.{Environment, EnvironmentUpdate}
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation, Stop}
import it.unibo.demo.{ID, Info, Position}
import it.unibo.mqtt.MqttContext

import scala.collection.concurrent.TrieMap

enum Actuation:
  case Rotation(rotationVector: (Double, Double))
  case Forward(direction: (Double, Double), distanceToGoal: Double)
  case NoOp
  case Stop

class RobotUpdateMqtt(angleThreshold: Double)(using MqttContext)
    extends EnvironmentUpdate[ID, Position, Actuation, Info, Environment[ID, Position, Info]]:

  // Small angular tolerance to avoid oscillations when almost aligned (in radians)
  private val angleTolerance = angleThreshold * math.Pi / 180.0

  // Control parameters for smooth movement
  private val maxSpeed = 0.9
  private val rotationGain = 0.8 // Proportional gain (Kp)
  private val speedScale = 1.0 // Scale factor for the calculated motor speeds
  private val maxWheelSpeed = 1.0 // Absolute wheel speed the motor protocol accepts

  // State maps for PD control and noise filtering per robot ID.
  // UpdateLoop.step drives the per-robot updates with parTraverse, so these are touched concurrently.
  private val previousErrors = TrieMap[ID, Double]()
  private val previousDerivatives = TrieMap[ID, Double]()
  private val previousLeftSpeeds = TrieMap[ID, Double]()
  private val previousRightSpeeds = TrieMap[ID, Double]()
  private val lastActuations = TrieMap[ID, String]()

  // Advanced filtering gains
  private val derivativeGain = 0.5 // Derivative gain (Kd) for angular damping
  private val speedSmoothingFactor = 0.6 // EMA filter coefficient for wheel speeds (beta)
  private val derivativeSmoothingFactor = 0.4 // EMA filter coefficient for the derivative term (suppresses sensor noise)
  private val motorDeadband = 0.10 // Motor speed threshold below which robot is stopped to prevent humming/jitter
  private val distanceTolerance = 0.03 // Distance threshold (3 cm) below which the robot stops completely to avoid tiny oscillations at the destination
  private val slowdownRadius = 0.20 // Distance (20 cm) within which the robot starts decelerating towards the goal

  private def normalizeAngle(a: Double): Double =
    var x = a
    while x <= -math.Pi do x += 2 * math.Pi
    while x > math.Pi do x -= 2 * math.Pi
    x

  /** Forget the PD/EMA history of a robot, so a restart does not inherit stale state. */
  private def resetControlState(id: ID): Unit =
    previousErrors.remove(id)
    previousDerivatives.remove(id)
    previousLeftSpeeds.put(id, 0.0)
    previousRightSpeeds.put(id, 0.0)

  /**
   * Scale a wheel speed pair down proportionally when it exceeds what the motors accept.
   * Clamping each wheel independently (as the firmware does) would flatten the differential
   * between them, making the robot drive straighter than commanded exactly when it is turning hardest.
   */
  private def saturate(left: Double, right: Double): (Double, Double) =
    val peak = math.max(math.abs(left), math.abs(right))
    if peak > maxWheelSpeed then (left * maxWheelSpeed / peak, right * maxWheelSpeed / peak)
    else (left, right)

  /** Low-pass filter the wheel speeds, then drop them entirely when both sit inside the motor deadband. */
  private def smoothAndApplyDeadband(id: ID, rawLeft: Double, rawRight: Double): (Double, Double) =
    val prevLeft = previousLeftSpeeds.getOrElse(id, 0.0)
    val prevRight = previousRightSpeeds.getOrElse(id, 0.0)

    val smoothLeft = speedSmoothingFactor * rawLeft + (1.0 - speedSmoothingFactor) * prevLeft
    val smoothRight = speedSmoothingFactor * rawRight + (1.0 - speedSmoothingFactor) * prevRight

    val (finalLeft, finalRight) =
      if math.abs(smoothLeft) < motorDeadband && math.abs(smoothRight) < motorDeadband then (0.0, 0.0)
      else (smoothLeft, smoothRight)

    previousLeftSpeeds.put(id, finalLeft)
    previousRightSpeeds.put(id, finalRight)
    (finalLeft, finalRight)

  private def calculateWheelSpeeds(id: ID, baseSpeed: Double, angularError: Double, moveForward: Boolean): (Double, Double) =
    if baseSpeed.isNaN || angularError.isNaN then
      previousLeftSpeeds.put(id, 0.0)
      previousRightSpeeds.put(id, 0.0)
      (0.0, 0.0)
    else
      val direction = if moveForward then 1.0 else -1.0

      // PD Controller for steering to actively dampen oscillations
      val prevError = previousErrors.getOrElse(id, angularError)
      val rawDerivative = angularError - prevError
      previousErrors.put(id, angularError)

      // Filter the derivative term to suppress high-frequency sensor noise
      val prevDerivative = previousDerivatives.getOrElse(id, 0.0)
      val errorDerivative = derivativeSmoothingFactor * rawDerivative + (1.0 - derivativeSmoothingFactor) * prevDerivative
      previousDerivatives.put(id, errorDerivative)

      // Calculate rotation correction (not scaled down near goal to preserve precise steering alignment)
      val rotationCorrection = angularError * rotationGain + errorDerivative * derivativeGain

      val leftBase = baseSpeed * direction
      val rightBase = baseSpeed * direction

      val (rawLeft, rawRight) = saturate((leftBase - rotationCorrection) * speedScale, (rightBase + rotationCorrection) * speedScale)
      smoothAndApplyDeadband(id, rawLeft, rawRight)

  private def getOrientation(world: Environment[ID, Position, Info], id: ID): Double =
    world.sensing(id).get("orientation") match {
      case Some(d: Double) => d
      case Some(d: java.lang.Double) => d.doubleValue()
      case Some(f: Float) => f.toDouble
      case Some(s: String) => s.toDoubleOption.getOrElse(0.0)
      case _ => 0.0
    }

  private def isDegenerate(vector: (Double, Double)): Boolean =
    vector._1.isNaN || vector._2.isNaN || (math.abs(vector._1) < 1e-9 && math.abs(vector._2) < 1e-9)

  override def update(world: Environment[ID, Position, Info], id: ID, actuation: Actuation): IO[Unit] =
    val lastAct = lastActuations.getOrElse(id, "")
    actuation match
      case _ if !world.nodes.contains(id) => IO(RobotMqttProtocol.nop(id))
      case NoOp =>
        lastActuations.put(id, "NoOp")
        IO(RobotMqttProtocol.nop(id))
      case Stop =>
        lastActuations.put(id, "Stop")
        // Reset state on Stop to avoid lag when restarting
        resetControlState(id)
        IO(RobotMqttProtocol.stop(id))
      case Rotation(target) =>
        IO {
          if lastAct != "Rotation" then resetControlState(id)
          lastActuations.put(id, "Rotation")

          val orientation = getOrientation(world, id) // current heading angle (robot frame)
          val currentVector = (-Math.sin(orientation), Math.cos(orientation))
          if isDegenerate(target) then
            resetControlState(id)
            RobotMqttProtocol.stop(id)
          else
            val currentAngle = math.atan2(currentVector._2, currentVector._1)
            val targetAngle = math.atan2(target._2, target._1)
            val deltaAngle = normalizeAngle(targetAngle - currentAngle)
            if deltaAngle.isNaN || math.abs(deltaAngle) < angleTolerance then
              resetControlState(id)
              RobotMqttProtocol.stop(id)
            else
              // Smooth rotation with proportional speed control to avoid overshoot
              val spinSpeed = math.max(0.3, math.min(0.8, math.abs(deltaAngle) * rotationGain))
              val directionFactor = if deltaAngle > 0 then 1.0 else -1.0
              val (rawLeft, rawRight) = saturate(-spinSpeed * directionFactor, spinSpeed * directionFactor)
              val (finalLeft, finalRight) = smoothAndApplyDeadband(id, rawLeft, rawRight)
              RobotMqttProtocol.moveWith(id, finalLeft, finalRight)
        }

      case Forward(direction, distanceToGoal) =>
        IO {
          if lastAct != "Forward" then resetControlState(id)
          lastActuations.put(id, "Forward")

          // Current heading (transform from stored angle to unit vector)
          val orientation = getOrientation(world, id)
          val currentVector = (-Math.sin(orientation), Math.cos(orientation))
          val currentAngle = math.atan2(currentVector._2, currentVector._1)

          if isDegenerate(direction) then
            resetControlState(id)
            RobotMqttProtocol.stop(id)
          else
            val targetAngle = math.atan2(direction._2, direction._1)

            val deltaForward = normalizeAngle(targetAngle - currentAngle)
            val desiredHeadingForBackward = normalizeAngle(targetAngle - math.Pi)
            val deltaBackward = normalizeAngle(desiredHeadingForBackward - currentAngle)

            val useForwardPlan = math.abs(deltaForward) <= math.abs(deltaBackward)
            val chosenDelta = if useForwardPlan then deltaForward else deltaBackward

            // Proportional slowdown factor when approaching the target to prevent oscillation
            val distanceFactor = math.max(0.45, math.min(1.0, distanceToGoal / slowdownRadius))

            if distanceToGoal.isNaN || distanceToGoal < distanceTolerance || chosenDelta.isNaN then
              // Goal reached: stop completely and reset state to avoid tiny corrections due to sensor noise
              resetControlState(id)
              RobotMqttProtocol.stop(id)
            else
              // Continuous differential drive controller.
              // Linear base speed scales with the cosine of the angular error: slower when turning sharply, full speed when aligned
              val baseSpeed = maxSpeed * math.cos(chosenDelta) * distanceFactor
              val (left, right) = calculateWheelSpeeds(id, baseSpeed, chosenDelta, useForwardPlan)
              RobotMqttProtocol.moveWith(id, left, right)
        }
