package it.unibo.demo.provider

import it.unibo.core.{Environment, EnvironmentProvider}
import it.unibo.demo.environment.MqttEnvironment
import it.unibo.demo.provider.MqttProtocol.{Formation, Neighborhood, RobotPosition}
import it.unibo.demo.{ID, Info, Position}
import it.unibo.mqtt.MqttContext
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

  // Retained payload: {"program": String, "leaderId": String (6-hex) | null, "params": {String: Double}} (see apps/dashboard/shared/protocol.ts).
  object Formation:
    val topic: String = "/config/formation"

  given RW[RobotPosition] = macroRW

class MqttProvider(var initialConfiguration: Map[String, Any])(using ExecutionContext, MqttContext) extends EnvironmentProvider[ID, Position, Info, Environment[ID, Position, Info]]:
  private val worldMap: ConcurrentMap[ID, (Position, Info)] = ConcurrentHashMap()
  private val neighborhood: ConcurrentMap[ID, Set[ID]] = ConcurrentHashMap()
  override def provide(): Future[Environment[ID, Position, Info]] = Future:
    val newNeighborhood = neighborhood.asScala.toMap
    val newWorldMap = worldMap.asScala.toMap
    newNeighborhood.map:
      case (id, neigh) => neigh.intersect(newWorldMap.keySet)
    val currentWorld = MqttEnvironment(newWorldMap, newNeighborhood)
    worldMap.clear()
    //neighborhood.clear()
    currentWorld
  def start(): Unit =
    val client = summon[MqttContext].client
    client.connect()
    client.subscribeWithResponse(RobotPosition.topic, (topic: String, message: MqttMessage) => {
      val robot = read[MqttProtocol.RobotPosition](message.getPayload)
      worldMap.put(robot.robot_id.toInt, ((robot.x, robot.y), initialConfiguration ++ Map("orientation" -> robot.orientation)))
      ()
    })
    client.subscribeWithResponse(Formation.topic, (topic: String, message: MqttMessage) => {
      import ujson.*
      val command = ujson.read(message.getPayload)
      val params = command.obj.get("params").map(_.obj.toMap.view.mapValues(_.num).toMap).getOrElse(Map.empty)
      val leaderUpdate = command.obj.get("leaderId").flatMap(_.strOpt)
        .map(deviceId => Map("leader" -> Integer.parseInt(deviceId, 16)))
        .getOrElse(Map.empty)
      initialConfiguration = initialConfiguration ++ params ++ leaderUpdate + ("program" -> command("program").str)
      ()
    })
    client.subscribeWithResponse(Neighborhood.topic, (topic: String, message: MqttMessage) => {
      val extractId = topic.split("/")(1).toInt
      val robotNeighborhood = read[List[String]](message.getPayload).map(_.toInt).toSet + extractId
      neighborhood.put(extractId, read[List[String]](message.getPayload).map(_.toInt).toSet)
      ()
    })
