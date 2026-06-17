package it.unibo.demo.manager

import it.unibo.core.aggregate.AggregateIncarnation.EXPORT
import it.unibo.demo.serialization.ExportJsonCodec
import it.unibo.demo.manager.MqttExportBus.ExportMessage
import it.unibo.mqtt.MqttContext
import org.eclipse.paho.client.mqttv3.MqttMessage
import org.slf4j.LoggerFactory

import java.util.concurrent.ConcurrentHashMap

/** Exchanges scafi exports between runtimes over MQTT.
  *
  * Every owned node's export is published (as JSON) on `robots/{id}/export`; every other runtime's
  * exports are received from the same topic pattern and cached, so the local orchestrator can use them
  * as the neighbours' coordination messages.
  *
  * @param owns whether THIS runtime currently computes the given node (its own published exports are
  *             ignored on receipt, since the locally computed one is always fresher).
  */

object MqttExportBus:
  object ExportMessage:
    val topic: String = "robots/+/export"
final class MqttExportBus(owns: Int => Boolean)(using mqtt: MqttContext):
  private val logger = LoggerFactory.getLogger(classOf[MqttExportBus])
  private val received = new ConcurrentHashMap[Int, EXPORT]()
  private val TopicPattern = "robots/+/export"
  private def topic(id: Int): String = s"robots/$id/export"

  /** Publish an owned node's fresh export (retained, so a runtime joining later sees it immediately). */
  def publish(id: Int, ex: EXPORT): Unit =
    try mqtt.client.publish(topic(id), ExportJsonCodec.encode(ex).getBytes, 0, true)
    catch case e: Throwable => logger.warn(s"Failed to publish export for robot $id", e)

  /** Latest export received for a node computed by some OTHER runtime. */
  def receivedExport(id: Int): Option[EXPORT] = Option(received.get(id))

  def start(): Unit =
    val client = mqtt.client
    if !client.isConnected then client.connect()
    client.subscribeWithResponse(
      ExportMessage.topic,
      (t: String, message: MqttMessage) => {
        val id = t.split("/")(1).toInt
        // Ignore our own echoes: if we compute this node, our local export is authoritative.
        if !owns(id) then
          try received.put(id, ExportJsonCodec.decode(new String(message.getPayload)))
          catch case e: Throwable => logger.warn(s"Failed to decode export for robot $id", e)
        ()
      }
    )
    logger.info(s"Export bus subscribed to $TopicPattern")
