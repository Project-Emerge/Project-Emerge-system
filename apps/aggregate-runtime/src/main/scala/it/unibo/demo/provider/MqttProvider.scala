package it.unibo.demo.provider

import cats.effect.IO
import cats.effect.Ref
import cats.effect.std.Dispatcher
import it.unibo.core.{Environment, EnvironmentProvider}
import it.unibo.demo.environment.MqttEnvironment
import it.unibo.demo.provider.MqttProtocol.{Formation, Neighborhood, RobotPosition}
import it.unibo.demo.scenarios.{BaseDemo, CustomFormation, CustomSpec}
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

  // Retained payload: {"program": String, "leaderId": String (6-hex) | null, "anchor": "leader" | "auto",
  //                     "params": {String: Double},
  //                     "custom": {"kind": "points" | "cartesian" | "polar", ...} | null}
  // (see apps/dashboard/shared/protocol.ts). `custom` is optional and only read by the `custom`
  // program; omitting the key leaves whatever spec was published before it in place.
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
        val applied = for {
          command <- IO(ujson.read(message.getPayload))
          // `numOpt`, not `num`: `num` throws on a non-number, and that failure would take the
          // whole message down with it -- program, leaderId and anchor included. The dashboard's
          // schema guarantees numbers, but anything else publishing to this broker does not.
          params = command.obj.get("params")
            .flatMap(_.objOpt)
            .map(_.flatMap((key, value) => value.numOpt.map(key -> _)).toMap)
            .getOrElse(Map.empty[String, Double])
          // The geometry of a data-driven formation, compiled here rather than in a round.
          custom = CustomSpecCodec.fromCommand(command)
          // A null leaderId has to clear the previous choice, not leave it in place: otherwise
          // the runtime keeps rooting every gradient on a stale device id and the fleet never
          // gets the chance to elect its own leader.
          leader = command.obj.get("leaderId").flatMap(_.strOpt)
            .map(deviceId => Integer.parseInt(deviceId, 16))
            .getOrElse(BaseDemo.NoLeader)
          anchor = command.obj.get("anchor").flatMap(_.strOpt).getOrElse(BaseDemo.AnchorLeader)
          _ <- IO(custom.foreach {
            // Say so out loud. A rejected geometry replaces the stored one, so the fleet drops to
            // CustomFormation's ring fallback rather than keeping the shape it was holding -- this
            // log line is the only account of why the shape an author published never appeared.
            case CustomSpec.Invalid(reason) =>
              logger.warn(s"Rejected the published custom formation: $reason")
            case _ => ()
          })
          _ <- initialConfigRef.update { current =>
            // These three go last so that a shape parameter can never shadow them.
            val updated = current ++ params
              ++ custom.map(CustomFormation.SPEC_SENSING -> _).toMap
              ++ Map(
                BaseDemo.Leader -> leader,
                BaseDemo.Anchor -> anchor,
                BaseDemo.Program -> command("program").str
              )
            logger.info(s"Configuration updated via MQTT: $updated")
            updated
          }
        } yield ()
        // Without this a malformed payload disappears into an unhandled fiber error rather than a
        // log line, which is exactly the diagnostic whoever published it needs.
        applied.handleErrorWith { error =>
          IO(logger.warn("Ignored a malformed /config/formation payload", error))
        }
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
