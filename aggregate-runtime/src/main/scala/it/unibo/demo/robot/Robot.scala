package it.unibo.demo.robot

import org.eclipse.paho.client.mqttv3.*
import upickle.default.{macroRW, ReadWriter as RW, *}

object RobotMqttProtocol:
  // TODO Create a provide
  val client = MqttClient("tcp://localhost:1883", MqttClient.generateClientId())
  client.connect()
  case class RobotMovement(left: Double, right: Double)
  given RW[RobotMovement] = macroRW

  def spinRight(robot: Int): Unit =
    client.publish(s"robots/${robot}/move", write(RobotMovement(0.2, -0.2)).getBytes, 0, false)

  def spinLeft(robot: Int): Unit =
    client.publish(s"robots/${robot}/move", write(RobotMovement(-0.2, 0.2)).getBytes, 0, false)

  def nop(robot: Int): Unit =
    client.publish(s"robots/${robot}/move", write(RobotMovement(0, 0)).getBytes, 0, false)

  def forward(robot: Int): Unit =
    client.publish(s"robots/${robot}/move", write(RobotMovement(0.9, 0.9)).getBytes, 0, false)

  def backward(robot: Int): Unit =
    client.publish(s"robots/${robot}/move", write(RobotMovement(-0.9, -0.9)).getBytes, 0, false)


