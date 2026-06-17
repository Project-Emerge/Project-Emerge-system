package it.unibo.demo

import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.aggregate.DistributedAggregateOrchestrator
import it.unibo.core.{Boundary, Environment, UpdateLoop}
import it.unibo.demo.manager.{MqttExportBus, OffloadingManagerWebSocketClient}
import it.unibo.demo.provider.MqttProvider
import it.unibo.demo.robot.{Actuation, RobotUpdateMqtt}
import it.unibo.demo.scenarios.*
import it.unibo.mqtt.MqttContext
import it.unibo.utils.Position.given

import scala.concurrent.ExecutionContext.Implicits.global
import scala.concurrent.Future

private[demo] val BROKER_URL = System.getenv().getOrDefault("MQTT_URL", "tcp://localhost:1883")
private[demo] val PROGRAM_FREQUENCY: Double = 10 // Hz

/** Default sensing/configuration shared by every runtime instance. */
private[demo] val DEMO_CONFIGURATION: Map[String, Any] =
  Map(
    "program" -> "pointToLeader",
    "leader" -> 12,
    "collisionArea" -> 0.3,
    "stabilityThreshold" -> 0.1,
  ) ++ LineFormation.DEFAULTS
    ++ VFormation.DEFAULTS
    ++ VerticalLineFormation.DEFAULTS
    ++ CircleFormation.DEFAULTS
    ++ SquareFormation.DEFAULTS

/** The full set of demos selectable at runtime via the `program` sensor. */
private[demo] def allDemos: AllDemoToLoad = AllDemoToLoad(
  "pointToLeader" -> PointTheLeader(),
  "vShape" -> VFormation(),
  "squareShape" -> SquareFormation(),
  "circleShape" -> CircleFormation(),
  "lineShape" -> LineFormation(),
  "verticalLineShape" -> VerticalLineFormation(),
  "stop" -> Stop()
)

class BaseAggregateServiceExample(demoToLaunch: BaseDemo) extends App:
  it.unibo.demo.robot.ActuationCodec.register()
  given MqttContext(BROKER_URL) // This is needed for all services using MQTT (e.g., RobotUpdateMqtt, MqttProvider)
  val provider = MqttProvider(DEMO_CONFIGURATION)
  provider.start() // Start listening to MQTT topics
  private val offloadingManagerClient = OffloadingManagerWebSocketClient.fromEnvironment(provider)
  offloadingManagerClient.start()
  val update = RobotUpdateMqtt(angleThreshold = 10) // angle threshold in degrees, used to avoid oscillations when almost aligned

  // Distributed field: this central runtime OWNS the enabled robots, computes their rounds and publishes
  // their exports over MQTT, while consuming the exports of the offloaded robots (computed by their edge
  // runtimes). A disabled robot is never hidden from the others — it stays in the environment as a
  // neighbour, we just take its export from the bus instead of computing it here.
  val exportBus = MqttExportBus(provider.isEnabled)
  exportBus.start()
  val drivingOrchestrator = DistributedAggregateOrchestrator[Position, Actuation](
    demoToLaunch,
    ownedNodes = _.nodes.filter(provider.isEnabled),
    publishExport = exportBus.publish,
    receivedExport = exportBus.receivedExport
  )

  // This is needed for the pipeline to work, but we do not want to render anything since we have a remote MQTT visualization
  val render = new Boundary[ID, Position, Info]:
    override def output(environment: Environment[ID, Position, Info]): Future[Unit] =
      Future.successful(())

  // Main loop, DO NOT CHANGE THIS!
  UpdateLoop.loop((1 / PROGRAM_FREQUENCY * 1000).toLong)( // in milliseconds, sleep time between two iterations
    provider,
    drivingOrchestrator,
    update,
    render
  )

/** Main aggregate computing class, it loads and runs the selected demo
  *
  * @param demos
  *   a list of pairs (name, demo) where name is the name of the demo to be used as identifier and demo is the actual
  *   demo instance
  */
class AllDemoToLoad(demos: (String, BaseDemo)*) extends BaseDemo {
  private val demosToMap: Map[String, BaseDemo] = demos.toMap

  override def main(): EXPORT = {
    val ctx = vm.context
    val currentProgram = sense[String]("program")
    align(currentProgram){
      programToLaunch => demosToMap(currentProgram)(ctx)
    }
  }
}

// The main object used to launch the demo, if you want, add more demos to the list
object ResearchNightDemos extends BaseAggregateServiceExample(allDemos)
