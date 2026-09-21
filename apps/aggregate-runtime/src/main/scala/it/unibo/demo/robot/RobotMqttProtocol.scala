package it.unibo.demo.robot

import it.unibo.mqtt.MqttContext
import ujson.Obj

/** MQTT motor commands for DropBot robots on `/motors/<6 hex digits>` at QoS 0. */
object RobotMqttProtocol:

  private def motorTopic(robot: Int): String = f"/motors/$robot%06X"

  private def clamp(value: Double): Double =
    if value.isNaN then 0.0 else math.max(-1.0, math.min(1.0, value))

  def moveWith(robot: Int, left: Double, right: Double)(using mqttContext: MqttContext): Unit =
    val payload = Obj("Move" -> Obj("left" -> clamp(left), "right" -> clamp(right)))
    mqttContext.client.publish(motorTopic(robot), ujson.write(payload).getBytes, 0, false)

  def stop(robot: Int)(using mqttContext: MqttContext): Unit =
    mqttContext.client.publish(motorTopic(robot), "\"Stop\"".getBytes, 0, false)
