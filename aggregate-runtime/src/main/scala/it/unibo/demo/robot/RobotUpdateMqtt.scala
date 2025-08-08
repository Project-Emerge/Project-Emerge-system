package it.unibo.demo.robot

import it.unibo.core.{Environment, EnvironmentUpdate}
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation}
import it.unibo.demo.{ID, Info, Position}

import scala.concurrent.{ExecutionContext, Future}

enum Actuation:
  case Rotation(rotationVector: (Double, Double))
  case Forward(vector: (Double, Double))
  case NoOp

class RobotUpdateMqtt(threshold: Double)(using ExecutionContext)
    extends EnvironmentUpdate[ID, Position, Actuation, Info, Environment[ID, Position, Info]]:

  override def update(world: Environment[ID, Position, Info], id: ID, actuation: Actuation): Future[Unit] =
    actuation match
      case _ if !world.nodes.contains(id) => Future(RobotMqttProtocol.nop(id))
      case NoOp => Future(RobotMqttProtocol.nop(id))
      case Rotation(actuation) =>
        Future:
          val direction = world.sensing(id)
          //println((id, direction))
          val directionVector = (Math.cos(direction), Math.sin(direction))
          val rotationVector = (actuation._1, actuation._2)
          val rotationEuclideanDistance = Math.sqrt(
            Math.pow(rotationVector._1 - directionVector._1, 2) +
              Math.pow(rotationVector._2 - directionVector._2, 2)
          )

          var deltaAngle =
            math.atan2(rotationVector._2, rotationVector._1) - Math.atan2(directionVector._2, directionVector._1)
          if (deltaAngle > math.Pi)
            deltaAngle = deltaAngle - 2 * math.Pi
          else if (deltaAngle < -math.Pi)
            deltaAngle = deltaAngle + 2 * math.Pi
          val angularDistance = Math.min(Math.abs(deltaAngle), 2 * Math.PI - Math.abs(deltaAngle))
          angularDistance match
            case _ if rotationEuclideanDistance < threshold => RobotMqttProtocol.nop(id)
            case _ if deltaAngle > 0 => RobotMqttProtocol.spinRight(id)
            case _ => RobotMqttProtocol.spinRight(id)
      case Forward(actuation) =>
        Future:
          val direction = world.sensing(id)
          val directionVector = (Math.cos(direction), Math.sin(direction))
          val adjustedVector = (actuation._1, actuation._2)
          val vectorDirection = Math.atan2(actuation._2, actuation._1)
          val vector = if (vectorDirection < 0) (-adjustedVector._1, -adjustedVector._2) else adjustedVector
          val forwardEuclideanDistance = Math.sqrt(
            Math.pow(vector._1 - directionVector._1, 2) +
              Math.pow(vector._2 - directionVector._2, 2)
          )
          var deltaAngle = Math.atan2(vector._2, vector._1) - Math.atan2(directionVector._2, directionVector._1)
          if (deltaAngle > math.Pi)
            deltaAngle = deltaAngle - 2 * math.Pi
          else if (deltaAngle < -math.Pi)
            deltaAngle = deltaAngle + 2 * math.Pi
          val angularDistance = Math.min(Math.abs(deltaAngle), 2 * Math.PI - Math.abs(deltaAngle))
          angularDistance match
            case _ if forwardEuclideanDistance < threshold => //RobotMqttProtocol.nop(id)
              if (vectorDirection < 0) RobotMqttProtocol.backward(id) else RobotMqttProtocol.forward(id)
            case _ if deltaAngle > 0 => RobotMqttProtocol.spinLeft(id)
            case _ => RobotMqttProtocol.spinLeft(id)
            // convert from -pi, pi to 0 to 2pi
  def adjustAngle(angle: Double): Double =
    if angle < 0 then angle + 2 * Math.PI else angle
