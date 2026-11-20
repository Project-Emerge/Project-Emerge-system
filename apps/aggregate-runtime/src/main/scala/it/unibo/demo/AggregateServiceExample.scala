package it.unibo.demo

import cats.effect.{IO, IOApp, Resource, Ref}
import cats.effect.std.Dispatcher
import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.core.{Boundary, Environment, UpdateLoop}
import it.unibo.demo.formation.{FormationCompiler, Fuel, SnippetKind}
import it.unibo.demo.provider.{FormationCheckService, MqttProvider, TimedPose}
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
    designedProgram(currentProgram) match {
      // Keyed by the program's id as well as its name: two designed programs share the name
      // `custom`, and a device must never resume one's `rep` state inside the other.
      case Some((id, program)) =>
        align(s"$currentProgram:$id") { _ => AllDemoToLoad.metered(program, ctx) }
      case None =>
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

  /** The program under `custom` when what was published there is a whole program, not a shape. */
  private def designedProgram(currentProgram: String): Option[(String, BaseDemo)] =
    if currentProgram != AllDemoToLoad.DesignedProgramName then None
    else sense[CustomSpec](CustomFormation.SPEC_SENSING) match {
      case CustomSpec.Program(id, program) => Some((id, program))
      case _ => None
    }
}

object AllDemoToLoad {
  /** The name a designed geometry and a designed program share on `/config/formation`. */
  val DesignedProgramName: String = "custom"

  /**
   * What one device's round of a designed program may spend. Some thirty devices run in turn
   * every 50 ms tick, so a runaway program costs each well under a millisecond.
   */
  val ProgramBudget: Fuel.Budget = Fuel.Budget(steps = 20_000L, bytes = 16L * 1024 * 1024)

  /**
   * One round of a designed program, metered. Running out is thrown on, to the orchestrator,
   * which isolates it like any failed round: that device is halted and its state dropped.
   */
  private def metered(program: BaseDemo, ctx: CONTEXT): EXPORT =
    Fuel.metered(ProgramBudget)(program(ctx)) match {
      case Right(round) => round
      case Left(outOfFuel) => throw outOfFuel
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

      // One compiler for the process: its symbol table is what makes every compile after the
      // first take milliseconds rather than seconds.
      val compiler = FormationCompiler()
      val provider = MqttProvider(configRef, worldMapRef, neighborhoodRef, compiler)
      val formationCheck = FormationCheckService(compiler)
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
        _         <- formationCheck.start()
        // Pays the cold compile now rather than on the first formation an operator publishes.
        _         <- IO.blocking(compiler.compile(SnippetKind.Geometry, "Formation(ctx => ring(ctx.count, 0.5))")).start
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
