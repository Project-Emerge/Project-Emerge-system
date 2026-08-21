package it.unibo.demo.robot

import it.unibo.core.{Environment, EnvironmentUpdate}
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation, Stop}
import it.unibo.demo.{ID, Info, Position}
import it.unibo.mqtt.MqttContext

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
  private val rotationGain = 0.8 // How aggressively to correct angular error
  private val minSpeed = 0.2 // Minimum speed to maintain forward momentum

  private def normalizeAngle(a: Double): Double =
    var x = a
    while x <= -math.Pi do x += 2 * math.Pi
    while x > math.Pi do x -= 2 * math.Pi
    x

  private def calculateWheelSpeeds(baseSpeed: Double, angularError: Double, moveForward: Boolean): (Double, Double) =
    val direction = if moveForward then 1.0 else -1.0
    val rotationCorrection = angularError * rotationGain
    val leftBase = baseSpeed * direction
    val rightBase = baseSpeed * direction
    
    val leftSpeed = leftBase - rotationCorrection
    val rightSpeed = rightBase + rotationCorrection
    
    val absAngularError = math.abs(angularError)
    val speedScale = 0.6
    
    (leftSpeed * speedScale, rightSpeed * speedScale)

  override def update(world: Environment[ID, Position, Info], id: ID, actuation: Actuation): Future[Unit] =
    actuation match
      case _ if !world.nodes.contains(id) => Future(RobotMqttProtocol.nop(id))
      case NoOp => Future(RobotMqttProtocol.nop(id))
      case Stop => Future(RobotMqttProtocol.stop(id))
      case Rotation(actuation) =>
        Future:
          val orientation = world.sensing(id)("orientation").asInstanceOf[Double] // current heading angle (robot frame)
          val currentVector = (-Math.sin(orientation), Math.cos(orientation))
          val targetVector = (actuation._1, actuation._2)
          val rotationEuclideanDistance = Math.sqrt(
            Math.pow(targetVector._1 - currentVector._1, 2) +
              Math.pow(targetVector._2 - currentVector._2, 2)
          )
          val currentAngle = math.atan2(currentVector._2, currentVector._1)
          val targetAngle = math.atan2(targetVector._2, targetVector._1)
          val deltaAngle = normalizeAngle(targetAngle - currentAngle)
          if (math.abs(deltaAngle) < angleTolerance) then
            RobotMqttProtocol.stop(id)
          else if deltaAngle > 0 then RobotMqttProtocol.spinLeft(id) else RobotMqttProtocol.spinRight(id)

      case Forward(desired) =>
        Future:
          // Current heading (transform from stored angle to unit vector)
          val orientation = world.sensing(id)("orientation").asInstanceOf[java.lang.Double]
          val currentVector = (-Math.sin(orientation), Math.cos(orientation))
          val currentAngle = math.atan2(currentVector._2, currentVector._1)
          val targetVector = (desired._1, desired._2)
          val targetAngle = math.atan2(targetVector._2, targetVector._1)

          val deltaForward = normalizeAngle(targetAngle - currentAngle)
          val desiredHeadingForBackward = normalizeAngle(targetAngle - math.Pi)
          val deltaBackward = normalizeAngle(desiredHeadingForBackward - currentAngle)

          val useForwardPlan = math.abs(deltaForward) <= math.abs(deltaBackward)
          val chosenDelta = if useForwardPlan then deltaForward else deltaBackward

          if math.abs(chosenDelta) < angleTolerance then
            if useForwardPlan then RobotMqttProtocol.forward(id) else RobotMqttProtocol.backward(id)
          else if chosenDelta > 0 then RobotMqttProtocol.spinLeft(id) else RobotMqttProtocol.spinRight(id)

