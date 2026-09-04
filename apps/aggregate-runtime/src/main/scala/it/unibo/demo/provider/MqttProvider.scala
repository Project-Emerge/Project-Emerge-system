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

import scala.concurrent.duration.*

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

/** The latest pose of one robot, with the local time it arrived so it can be aged out. */
final case class TimedPose(position: Position, info: Info, receivedAt: FiniteDuration)

class MqttProvider(
    val initialConfigRef: Ref[IO, Map[String, Any]],
    private val worldMapRef: Ref[IO, Map[ID, TimedPose]],
    private val neighborhoodRef: Ref[IO, Map[ID, Set[ID]]],
    private val poseTtl: FiniteDuration = MqttProvider.defaultPoseTtl
)(using dispatcher: Dispatcher[IO], mqttContext: MqttContext) extends EnvironmentProvider[ID, Position, Info, Environment[ID, Position, Info]]:

  private val logger = LoggerFactory.getLogger(classOf[MqttProvider])

  /**
   * A snapshot of every robot seen within [[poseTtl]].
   *
   * Poses are kept, not drained: a robot briefly hidden from the cameras used to vanish from the
   * environment for a tick, which meant no command was computed for it and it carried on executing
   * whatever it was last told to do. Holding the last pose for a short while keeps the aggregate
   * program running over a stable set of nodes, and anything older than the TTL is dropped so that
   * `MotorCommandPublisher` expires its command into a stop.
   */
  override def provide(): IO[Environment[ID, Position, Info]] =
    for {
      now             <- IO.monotonic
      neighborhoodRaw <- neighborhoodRef.get
      worldMap        <- worldMapRef.updateAndGet(_.filter { case (_, pose) => now - pose.receivedAt <= poseTtl })
      activeKeys       = worldMap.keySet
      newNeighborhood  = neighborhoodRaw.map {
        case (id, neigh) => id -> neigh.intersect(activeKeys)
      }.filter { case (id, _) => activeKeys.contains(id) }
    } yield MqttEnvironment(worldMap.view.mapValues(pose => (pose.position, pose.info)).toMap, newNeighborhood)

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
          now        <- IO.monotonic
          _          <- worldMapRef.update(_ + (robotId -> TimedPose(
                          (robot.x_m, robot.y_m),
                          config ++ Map("orientation" -> robot.heading_rad),
                          now
                        )))
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

object MqttProvider:
  /**
   * How long a pose stays usable. Comfortably longer than the vision system's 20 Hz publish
   * interval so ordinary jitter is invisible, and shorter than
   * `MotorCommandPublisher.commandTtl` so a robot that really is gone gets stopped promptly.
   */
  val defaultPoseTtl: FiniteDuration = 350.millis
