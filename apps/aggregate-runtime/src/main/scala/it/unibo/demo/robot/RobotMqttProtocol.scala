package it.unibo.demo.robot

import it.unibo.mqtt.MqttContext
import ujson.Obj

/**
 * The wire format the robots speak, mirroring `DriveCommand` in the DropBot firmware:
 * `{"Move":{"left":<f32>,"right":<f32>}}` or the JSON string `"Stop"`, on `/motors/<6 hex digits>`
 * at QoS 0.
 *
 * `left` and `right` are dimensionless and bounded to [-1, 1]. They are *not* proportional to
 * wheel speed - the firmware remaps them onto its stiction range - so build them with
 * [[DifferentialDrive.toWheels]] rather than by hand, and publish them through
 * [[MotorCommandPublisher]] rather than directly.
 */
object RobotMqttProtocol:

  private def motorTopic(robot: Int): String = f"/motors/$robot%06X"

  private def clamp(value: Double): Double =
    if value.isNaN then 0.0 else math.max(-1.0, math.min(1.0, value))

  def moveWith(robot: Int, left: Double, right: Double)(using mqttContext: MqttContext): Unit =
    val payload = Obj("Move" -> Obj("left" -> clamp(left), "right" -> clamp(right)))
    mqttContext.client.publish(motorTopic(robot), ujson.write(payload).getBytes, 0, false)

  def stop(robot: Int)(using mqttContext: MqttContext): Unit =
    mqttContext.client.publish(motorTopic(robot), "\"Stop\"".getBytes, 0, false)
