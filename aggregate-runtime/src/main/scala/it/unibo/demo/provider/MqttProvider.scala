package it.unibo.demo.provider

import it.unibo.core.{Environment, EnvironmentProvider}
import it.unibo.demo.environment.MqttEnvironment
import it.unibo.demo.provider.MqttProtocol.{Neighborhood, RobotPosition}
import it.unibo.demo.{ID, Info, Position}
import org.eclipse.paho.client.mqttv3.*
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence
import upickle.default.{macroRW, ReadWriter as RW, *}

import java.util.concurrent.{ConcurrentHashMap, ConcurrentMap}
import scala.concurrent.{ExecutionContext, Future}
import scala.jdk.CollectionConverters.MapHasAsScala

object MqttProtocol:
  case class RobotPosition(robot_id: String, x: Double, y: Double, orientation: Double)
  object RobotPosition:
    val topic: String = "robots/+/position"

  object Neighborhood:
    val topic: String = "robots/+/neighbors"
  given RW[RobotPosition] = macroRW

class MqttProvider(private val url: String)(using ExecutionContext) extends EnvironmentProvider[ID, Position, Info, Environment[ID, Position, Info]]:
  private val worldMap: ConcurrentMap[ID, (Position, Info)] = ConcurrentHashMap()
  private val neighborhood: ConcurrentMap[ID, Set[ID]] = ConcurrentHashMap()
  override def provide(): Future[Environment[ID, (Info, Info), Info]] = Future:
    val currentWorld = MqttEnvironment(worldMap.asScala.toMap, neighborhood.asScala.toMap)
    worldMap.clear()
    neighborhood.clear()
    currentWorld

  def start(): Unit =
    val client = MqttClient(url, MqttClient.generateClientId())
    client.connect()
    client.subscribeWithResponse(RobotPosition.topic, (topic: String, message: MqttMessage) => {
      val robot = read[MqttProtocol.RobotPosition](message.getPayload)
      worldMap.put(robot.robot_id.toInt, ((robot.x, robot.y), robot.orientation))
      ()
    })
    client.subscribeWithResponse(Neighborhood.topic, (topic: String, message: MqttMessage) => {
      val extractId = topic.split("/")(1).toInt
      neighborhood.put(extractId, read[List[String]](message.getPayload).map(_.toInt).toSet)
      ()
    })
