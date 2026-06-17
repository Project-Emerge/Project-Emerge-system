package it.unibo.demo

import it.unibo.core.aggregate.DistributedAggregateOrchestrator
import it.unibo.core.{Boundary, Environment, Orchestrator, UpdateLoop}
import it.unibo.demo.manager.{MqttExportBus, RobotOffloadingClient}
import it.unibo.demo.provider.MqttProvider
import it.unibo.demo.robot.{Actuation, RobotUpdateMqtt}
import it.unibo.mqtt.MqttContext
import it.unibo.utils.Position.given

import java.util.concurrent.atomic.{AtomicBoolean, AtomicLong}
import scala.concurrent.ExecutionContext.Implicits.global
import scala.concurrent.Future

/** Aggregate runtime running "on a single robot" (edge computing) that also takes part in the
  * offloading protocol.
  *
  * It is meant to run in its own container, one per robot, taking the robot id from the `ROBOT_ID`
  * environment variable (or the first argument). On startup it:
  *
  *   1. connects to the offloading-manager as the robot (`/ws/robots/{id}`) and asks to offload its
  *      aggregate computation to the central runtime;
  *   2. when NOT offloaded, it OWNS its robot in the distributed field: it computes only that node's
  *      aggregate round (using the neighbours' exports received over MQTT), publishes the node's export
  *      back on the bus, and actuates the robot. When offloaded it owns nothing — the central runtime
  *      computes and drives the robot, and this instance only perceives it through the bus.
  *
  * So when offloading is ON the central runtime drives the robot and this edge instance stays idle;
  * when offloading is turned OFF (e.g. from the dashboard) this instance takes over and drives the
  * robot locally. The robot keeps moving seamlessly across the hand-off, on one coherent field.
  */
object EdgeRobotRuntime:
  import upickle.default.{macroRW, ReadWriter as RW, write}

  final case class Telemetry(
      id: ID,
      tick: Long,
      ts: Long,
      offloaded: Boolean,
      actuating: Boolean,
      computed: Boolean,
      actuation: String,
      program: String,
      leader: String,
      neighbors: List[ID],
      worldSize: Int,
      exportPaths: Int
  )
  given RW[Telemetry] = macroRW

  def main(args: Array[String]): Unit =
    it.unibo.demo.robot.ActuationCodec.register()
    val robotId: Int = args.headOption
      .orElse(Option(System.getenv("ROBOT_ID")))
      .map(_.trim)
      .flatMap(_.toIntOption)
      .getOrElse(
        throw IllegalArgumentException(
          "EdgeRobotRuntime requires a robot id via the ROBOT_ID env variable or the first argument"
        )
      )

    given MqttContext(BROKER_URL)
    val provider = MqttProvider(DEMO_CONFIGURATION)
    provider.start()

    // true  => offloaded to the central runtime, this edge instance stays idle
    // false => not offloaded, this edge instance drives the robot locally
    val offloaded = new AtomicBoolean(true)
    val robotClient = RobotOffloadingClient.fromEnvironment(
      robotId,
      onOffloadChange = aggregate =>
        if offloaded.getAndSet(aggregate) != aggregate then
          if aggregate then println(s"Robot $robotId offloaded to central runtime — pausing local computation")
          else println(s"Robot $robotId offloading OFF — taking over locally")
    )
    robotClient.start()

    // The edge owns its robot only while NOT offloaded. It exchanges exports with the rest of the system
    // over MQTT: it publishes its node's export and receives the neighbours' exports so it perceives the
    // whole world (its neighbourhood view stays up to date) even though it computes a single node.
    val exportBus = MqttExportBus(id => !offloaded.get() && id == robotId)
    exportBus.start()
    val inner = DistributedAggregateOrchestrator[Position, Actuation](
      allDemos,
      ownedNodes = world => if offloaded.get() then Set.empty[ID] else Set(robotId).intersect(world.nodes),
      publishExport = exportBus.publish,
      receivedExport = exportBus.receivedExport
    )

    // Telemetry on `edge/{id}/status` (consumed by edge.html): when offloaded the edge computes nothing
    // (the central does); when not offloaded it computes and drives its node.
    val telemetryTopic = s"edge/$robotId/status"
    val tickCounter = new AtomicLong(0)
    def formatVector(v: (Double, Double)): String = f"${v._1}%.2f, ${v._2}%.2f"
    def describe(a: Option[Actuation]): String = a match
      case Some(Actuation.Forward(v))  => s"Forward(${formatVector(v)})"
      case Some(Actuation.Rotation(v)) => s"Rotation(${formatVector(v)})"
      case Some(Actuation.Stop)        => "Stop"
      case Some(Actuation.NoOp)        => "NoOp"
      case None                        => "—"

    val orchestrator = new Orchestrator[ID, Position, Info, Actuation]:
      override def tick(world: Environment[ID, Position, Info]): Map[ID, Actuation] =
        val out = inner.tick(world)
        val own = out.get(robotId)
        val computed = own.isDefined
        val n = tickCounter.incrementAndGet()
        // What the node perceives/uses this round, so the monitor can show *how* it is evaluating.
        val present = world.nodes
        val neighbors = (world.neighbors(robotId) intersect present).toList.sorted
        val info = if present.contains(robotId) then world.sensing(robotId) else Map.empty[String, Any]
        val program = info.get("program").map(_.toString).getOrElse("?")
        val leader = info.get("leader").map(_.toString).getOrElse("?")
        val exportPaths = inner.lastExports.get(robotId).map(_.paths.size).getOrElse(0)
        val json = write(Telemetry(
          id = robotId,
          tick = n,
          ts = System.currentTimeMillis(),
          offloaded = offloaded.get(),
          actuating = computed,
          computed = computed,
          actuation = describe(own),
          program = program,
          leader = leader,
          neighbors = neighbors,
          worldSize = present.size,
          exportPaths = exportPaths
        ))
        summon[MqttContext].client.publish(telemetryTopic, json.getBytes, 0, true)
        out
    val update = RobotUpdateMqtt(angleThreshold = 10)
    val render = new Boundary[ID, Position, Info]:
      override def output(environment: Environment[ID, Position, Info]): Future[Unit] = Future.successful(())

    println(s"Edge runtime started for robot $robotId (broker: $BROKER_URL)")
    UpdateLoop.loop((1 / PROGRAM_FREQUENCY * 1000).toLong)(provider, orchestrator, update, render)
