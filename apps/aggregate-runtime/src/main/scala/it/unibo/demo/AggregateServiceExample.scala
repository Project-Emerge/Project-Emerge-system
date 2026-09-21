package it.unibo.demo

import cats.effect.{IO, IOApp, Resource, Ref}
import cats.effect.std.Dispatcher
import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.core.{Boundary, Environment, UpdateLoop}
import it.unibo.demo.provider.{MqttProvider, TimedPose}
import it.unibo.demo.robot.{Actuation, DriveConfig, MotorCommandPublisher, RobotUpdateMqtt}
import it.unibo.demo.scenarios.*
import it.unibo.mqtt.MqttContext
import it.unibo.utils.Position.given
import org.slf4j.LoggerFactory
import scala.concurrent.duration.*

private val BROKER_URL = System.getenv().getOrDefault("MQTT_URL", "tcp://localhost:1883")
private val PROGRAM_FREQUENCY: Double = 20 // Hz

class AllDemoToLoad(demos: (String, BaseDemo)*) extends BaseDemo {
  private val logger = LoggerFactory.getLogger(classOf[AllDemoToLoad])
  private val demosToMap: Map[String, BaseDemo] = demos.toMap

  override def main(): EXPORT = {
    val ctx = vm.context
    val currentProgram = sense[String](BaseDemo.Program)
    val programToRun = demosToMap.get(currentProgram) match {
      case Some(demo) => demo
      case None =>
        logger.warn(s"Unknown program name: '$currentProgram'. Falling back to 'NoOp'. Available: ${demosToMap.keys.mkString(", ")}")
        demosToMap.getOrElse("stop", NoOp())
    }
    align(currentProgram){
      programToLaunch => programToRun(ctx)
    }
  }
}

object ResearchNightDemos extends IOApp.Simple:

  override def run: IO[Unit] =
    val defaults = FormationDefaults.All

    val makeResources: Resource[IO, (MqttContext, Dispatcher[IO], Ref[IO, Map[String, Any]], Ref[IO, Map[ID, TimedPose]], Ref[IO, Map[ID, Set[ID]]])] =
      for {
        dispatcher      <- Dispatcher.parallel[IO]
        // Wrap the MqttContext inside a Resource to ensure proper lifecyle/cleanup
        mqttContext     <- Resource.make(IO(MqttContext(BROKER_URL)))(ctx => IO {
                             if (ctx.client.isConnected) {
                               ctx.client.disconnect()
                               ctx.client.close()
                             }
                           }.handleErrorWith(_ => IO.unit))
        configRef       <- Resource.eval(Ref.of[IO, Map[String, Any]](defaults))
        worldMapRef     <- Resource.eval(Ref.of[IO, Map[ID, TimedPose]](Map.empty))
        neighborhoodRef <- Resource.eval(Ref.of[IO, Map[ID, Set[ID]]](Map.empty))
      } yield (mqttContext, dispatcher, configRef, worldMapRef, neighborhoodRef)

    makeResources.use { case (mqttContext, dispatcher, configRef, worldMapRef, neighborhoodRef) =>
      given MqttContext = mqttContext
      given Dispatcher[IO] = dispatcher

      val provider = MqttProvider(configRef, worldMapRef, neighborhoodRef)
      val demoToLaunch = AllDemoToLoad(FormationDefaults.Programs*)
      val aggregateOrchestrator =
        AggregateOrchestrator[Position, Actuation](demoToLaunch, haltOnFailure = Some(Actuation.Stop))

      val render = new Boundary[ID, Position, Info]:
        override def output(environment: Environment[ID, Position, Info]): IO[Unit] =
          IO.unit

      val driveConfig = DriveConfig.fromEnvironment

      for {
        _         <- IO(LoggerFactory.getLogger("it.unibo.demo").info(s"Drive configuration: $driveConfig"))
        publisher <- MotorCommandPublisher(driveConfig)
        update     = RobotUpdateMqtt(publisher, driveConfig)
        _         <- provider.start()
        loopDuration = (1 / PROGRAM_FREQUENCY * 1000).toLong
        _ <- publisher.run().background.use { _ =>
          UpdateLoop.loop(loopDuration)(
            provider,
            aggregateOrchestrator,
            update,
            render
          )
        }
      } yield ()
    }
