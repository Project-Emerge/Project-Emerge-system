package it.unibo.demo.robot

import it.unibo.core.{Environment, EnvironmentUpdate}
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation, Stop}
import it.unibo.demo.{ID, Info, Position}
import it.unibo.mqtt.MqttContext

import scala.collection.concurrent.TrieMap
import scala.concurrent.{ExecutionContext, Future}

enum Actuation:
  case Rotation(rotationVector: (Double, Double))
  case Forward(vector: (Double, Double))
  case NoOp
  case Stop

class RobotUpdateMqtt(angleThreshold: Double)(using ExecutionContext, MqttContext)
    extends EnvironmentUpdate[ID, Position, Actuation, Info, Environment[ID, Position, Info]]:

  // Small angular tolerance to avoid oscillations when almost aligned (in radians)
  private val angleTolerance = angleThreshold * math.Pi / 180.0 // 5 degrees
  
  // Control parameters for smooth movement
  private val maxSpeed = 0.9
  private val rotationGain = 0.8 // Proportional gain (Kp)
  private val speedScale = 0.6 // Scale factor for the calculated motor speeds

  // State maps for PD control and noise filtering per robot ID
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

  private def normalizeAngle(a: Double): Double =
    var x = a
    while x <= -math.Pi do x += 2 * math.Pi
    while x > math.Pi do x -= 2 * math.Pi
    x

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
      
      val leftSpeed = leftBase - rotationCorrection
      val rightSpeed = rightBase + rotationCorrection
      
      val rawLeft = leftSpeed * speedScale
      val rawRight = rightSpeed * speedScale

      // Low-pass filter (Exponential Moving Average) on output wheel speeds to smooth out sensor noise and latency
      val prevLeft = previousLeftSpeeds.getOrElse(id, 0.0)
      val prevRight = previousRightSpeeds.getOrElse(id, 0.0)

      val smoothLeft = speedSmoothingFactor * rawLeft + (1.0 - speedSmoothingFactor) * prevLeft
      val smoothRight = speedSmoothingFactor * rawRight + (1.0 - speedSmoothingFactor) * prevRight

      // Motor deadband: prevent tiny corrections from making the motors jitter without moving
      val (finalLeft, finalRight) = if math.abs(smoothLeft) < motorDeadband && math.abs(smoothRight) < motorDeadband then
        (0.0, 0.0)
      else
        (smoothLeft, smoothRight)

      previousLeftSpeeds.put(id, finalLeft)
      previousRightSpeeds.put(id, finalRight)

      (finalLeft, finalRight)

  override def update(world: Environment[ID, Position, Info], id: ID, actuation: Actuation): Future[Unit] =
    val lastAct = lastActuations.getOrElse(id, "")
    actuation match
      case _ if !world.nodes.contains(id) => Future(RobotMqttProtocol.nop(id))
      case NoOp => 
        lastActuations.put(id, "NoOp")
        Future(RobotMqttProtocol.nop(id))
      case Stop => 
        lastActuations.put(id, "Stop")
        // Reset state on Stop to avoid lag when restarting
        previousErrors.remove(id)
        previousDerivatives.remove(id)
        previousLeftSpeeds.put(id, 0.0)
        previousRightSpeeds.put(id, 0.0)
        Future(RobotMqttProtocol.stop(id))
      case Rotation(actuation) =>
        Future:
          if lastAct != "Rotation" then
            previousErrors.remove(id)
            previousDerivatives.remove(id)
            previousLeftSpeeds.put(id, 0.0)
            previousRightSpeeds.put(id, 0.0)
          lastActuations.put(id, "Rotation")

          val orientation = world.sensing(id)("orientation").asInstanceOf[Double] // current heading angle (robot frame)
          val currentVector = (-Math.sin(orientation), Math.cos(orientation))
          val targetVector = (actuation._1, actuation._2)
          if targetVector._1.isNaN || targetVector._2.isNaN || (math.abs(targetVector._1) < 1e-9 && math.abs(targetVector._2) < 1e-9) then
            previousErrors.remove(id)
            previousDerivatives.remove(id)
            previousLeftSpeeds.put(id, 0.0)
            previousRightSpeeds.put(id, 0.0)
            RobotMqttProtocol.stop(id)
          else
            val currentAngle = math.atan2(currentVector._2, currentVector._1)
            val targetAngle = math.atan2(targetVector._2, targetVector._1)
            val deltaAngle = normalizeAngle(targetAngle - currentAngle)
            if deltaAngle.isNaN || math.abs(deltaAngle) < angleTolerance then
              previousErrors.remove(id)
              previousDerivatives.remove(id)
              previousLeftSpeeds.put(id, 0.0)
              previousRightSpeeds.put(id, 0.0)
              RobotMqttProtocol.stop(id)
            else
              // Smooth rotation with proportional speed control to avoid overshoot
              val spinSpeed = math.max(0.3, math.min(0.8, math.abs(deltaAngle) * rotationGain))
              val directionFactor = if deltaAngle > 0 then 1.0 else -1.0
              val rawLeft = -spinSpeed * directionFactor
              val rawRight = spinSpeed * directionFactor

              // Low-pass filter (Exponential Moving Average) on rotation speeds
              val prevLeft = previousLeftSpeeds.getOrElse(id, 0.0)
              val prevRight = previousRightSpeeds.getOrElse(id, 0.0)
              val smoothLeft = speedSmoothingFactor * rawLeft + (1.0 - speedSmoothingFactor) * prevLeft
              val smoothRight = speedSmoothingFactor * rawRight + (1.0 - speedSmoothingFactor) * prevRight

              val (finalLeft, finalRight) = if math.abs(smoothLeft) < motorDeadband && math.abs(smoothRight) < motorDeadband then
                (0.0, 0.0)
              else
                (smoothLeft, smoothRight)

              previousLeftSpeeds.put(id, finalLeft)
              previousRightSpeeds.put(id, finalRight)
              println(s"[DEBUG ROTATION] Robot $id - deltaAngle: $deltaAngle, spinSpeed: $spinSpeed, finalLeft: $finalLeft, finalRight: $finalRight")
              RobotMqttProtocol.moveWith(id, finalLeft, finalRight)

      case Forward(desired) =>
        Future:
          if lastAct != "Forward" then
            previousErrors.remove(id)
            previousDerivatives.remove(id)
            previousLeftSpeeds.put(id, 0.0)
            previousRightSpeeds.put(id, 0.0)
          lastActuations.put(id, "Forward")

          // Current heading (transform from stored angle to unit vector)
          val orientation = world.sensing(id)("orientation").asInstanceOf[java.lang.Double]
          val currentVector = (-Math.sin(orientation), Math.cos(orientation))
          val currentAngle = math.atan2(currentVector._2, currentVector._1)
          val targetVector = (desired._1, desired._2)

          if targetVector._1.isNaN || targetVector._2.isNaN then
            previousErrors.remove(id)
            previousDerivatives.remove(id)
            previousLeftSpeeds.put(id, 0.0)
            previousRightSpeeds.put(id, 0.0)
            RobotMqttProtocol.stop(id)
          else
            val targetAngle = math.atan2(targetVector._2, targetVector._1)

            // Calculate target distance (magnitude of desired unnormalized vector)
            val distance = math.sqrt(targetVector._1 * targetVector._1 + targetVector._2 * targetVector._2)

            val deltaForward = normalizeAngle(targetAngle - currentAngle)
            val desiredHeadingForBackward = normalizeAngle(targetAngle - math.Pi)
            val deltaBackward = normalizeAngle(desiredHeadingForBackward - currentAngle)

            val useForwardPlan = math.abs(deltaForward) <= math.abs(deltaBackward)
            val chosenDelta = if useForwardPlan then deltaForward else deltaBackward

            // Proportional slowdown factor when approaching the target to prevent oscillation
            val slowdownRadius = 0.40 // 40 cm
            val distanceFactor = math.max(0.25, math.min(1.0, distance / slowdownRadius))

            if distance < distanceTolerance || distance.isNaN || chosenDelta.isNaN then
              // Goal reached: stop completely and reset state to avoid tiny corrections due to sensor noise
              previousErrors.remove(id)
              previousDerivatives.remove(id)
              previousLeftSpeeds.put(id, 0.0)
              previousRightSpeeds.put(id, 0.0)
              RobotMqttProtocol.stop(id)
            else
              // Continuous differential drive controller
              // Linear base speed scales with the cosine of the angular error: slower when turning sharply, full speed when aligned
              val baseSpeed = maxSpeed * math.cos(chosenDelta) * distanceFactor
              val (left, right) = calculateWheelSpeeds(id, baseSpeed, chosenDelta, useForwardPlan)
              RobotMqttProtocol.moveWith(id, left, right)
