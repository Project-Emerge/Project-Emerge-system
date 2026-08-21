package it.unibo.demo.robot

import it.unibo.mqtt.MqttContext
import ujson.Obj

object RobotMqttProtocol:

  private val rotationSpeed = 0.6
  private val forwardSpeed = 1.0

  private def motorTopic(robot: Int): String = f"/motors/$robot%06X"

  private def publishMove(robot: Int, left: Double, right: Double)(using mqttContext: MqttContext): Unit =
    val payload = Obj("Move" -> Obj("left" -> left, "right" -> right))
    mqttContext.client.publish(motorTopic(robot), ujson.write(payload).getBytes, 0, false)

  private def publishStop(robot: Int)(using mqttContext: MqttContext): Unit =
    mqttContext.client.publish(motorTopic(robot), "\"Stop\"".getBytes, 0, false)

  def spinRight(robot: Int)(using MqttContext): Unit = publishMove(robot, rotationSpeed, -rotationSpeed)

  def spinLeft(robot: Int)(using MqttContext): Unit = publishMove(robot, -rotationSpeed, rotationSpeed)

  def nop(robot: Int)(using MqttContext): Unit = ()

  def stop(robot: Int)(using MqttContext): Unit = publishStop(robot)

  def forward(robot: Int)(using MqttContext): Unit = publishMove(robot, forwardSpeed, forwardSpeed)

  def backward(robot: Int)(using MqttContext): Unit = publishMove(robot, -forwardSpeed, -forwardSpeed)

  def moveWith(robot: Int, left: Double, right: Double)(using MqttContext): Unit = publishMove(robot, left, right)
