package it.unibo.demo.provider

import cats.effect.IO
import cats.effect.Ref
import cats.effect.std.Dispatcher
import it.unibo.core.{Environment, EnvironmentProvider}
import it.unibo.demo.environment.MqttEnvironment
import it.unibo.demo.provider.MqttProtocol.{Formation, Neighborhood, RobotPosition}
import it.unibo.demo.{ID, Info, Position}
import it.unibo.mqtt.MqttContext
import org.eclipse.paho.client.mqttv3.*
import org.slf4j.LoggerFactory
import upickle.default.{macroRW, ReadWriter as RW, *}

object MqttProtocol:
  case class RobotPosition(
    x_m: Double,
    y_m: Double,
    heading_rad: Double,
    speed_m_s: Double = 0.0,
    position_variance_m2: Double = 0.0,
    timestamp_us: Long = 0L
  )
  object RobotPosition:
    val topic: String = "/pose/+"

  object Neighborhood:
    val topic: String = "/neighbors/+"

  // Retained payload: {"program": String, "leaderId": String (6-hex) | null, "params": {String: Double}} (see apps/dashboard/shared/protocol.ts).
  object Formation:
    val topic: String = "/config/formation"

  given RW[RobotPosition] = macroRW

class MqttProvider(
    val initialConfigRef: Ref[IO, Map[String, Any]],
    private val worldMapRef: Ref[IO, Map[ID, (Position, Info)]],
    private val neighborhoodRef: Ref[IO, Map[ID, Set[ID]]]
)(using dispatcher: Dispatcher[IO], mqttContext: MqttContext) extends EnvironmentProvider[ID, Position, Info, Environment[ID, Position, Info]]:

  private val logger = LoggerFactory.getLogger(classOf[MqttProvider])

  override def provide(): IO[Environment[ID, Position, Info]] =
    for {
      neighborhoodRaw <- neighborhoodRef.get
      worldMap        <- worldMapRef.getAndSet(Map.empty)
      activeKeys       = worldMap.keySet
      newNeighborhood  = neighborhoodRaw.map {
        case (id, neigh) => id -> neigh.intersect(activeKeys)
      }.filter { case (id, _) => activeKeys.contains(id) }
    } yield MqttEnvironment(worldMap, newNeighborhood)

  def start(): IO[Unit] = IO {
    val client = mqttContext.client
    if (!client.isConnected) {
      client.connect()
    }
    client.subscribeWithResponse(RobotPosition.topic, (topic: String, message: MqttMessage) => {
      dispatcher.unsafeRunAndForget {
        for {
          robot      <- IO(read[MqttProtocol.RobotPosition](message.getPayload))
          deviceId   = topic.split("/").last
          robotId    = Integer.parseInt(deviceId, 16)
          config     <- initialConfigRef.get
          _          <- worldMapRef.update(_ + (robotId -> ((robot.x_m, robot.y_m), config ++ Map("orientation" -> robot.heading_rad))))
        } yield ()
      }
    })
    client.subscribeWithResponse(Formation.topic, (topic: String, message: MqttMessage) => {
      dispatcher.unsafeRunAndForget {
        for {
          command <- IO(ujson.read(message.getPayload))
          params = command.obj.get("params").map(_.obj.toMap.view.mapValues(_.num).toMap).getOrElse(Map.empty)
          leaderUpdate = command.obj.get("leaderId").flatMap(_.strOpt)
            .map(deviceId => Map("leader" -> Integer.parseInt(deviceId, 16)))
            .getOrElse(Map.empty)
          _ <- initialConfigRef.update { current =>
            val updated = current ++ params ++ leaderUpdate + ("program" -> command("program").str)
            logger.info(s"Configuration updated via MQTT: $updated")
            updated
          }
        } yield ()
      }
    })
    client.subscribeWithResponse(Neighborhood.topic, (topic: String, message: MqttMessage) => {
      dispatcher.unsafeRunAndForget {
        for {
          deviceId   <- IO(topic.split("/").last)
          extractId  = Integer.parseInt(deviceId, 16)
          payloadSet <- IO(read[List[String]](message.getPayload).map(s => Integer.parseInt(s, 16)).toSet)
          robotNeighborhood = payloadSet + extractId
          _          <- neighborhoodRef.update(_ + (extractId -> robotNeighborhood))
        } yield ()
      }
    })
  }
