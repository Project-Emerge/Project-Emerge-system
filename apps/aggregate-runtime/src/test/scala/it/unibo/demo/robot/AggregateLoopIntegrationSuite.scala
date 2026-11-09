package it.unibo.demo.robot

import cats.effect.std.Dispatcher
import cats.effect.unsafe.implicits.global
import cats.effect.{IO, Ref, Resource}
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.core.{Boundary, Environment, UpdateLoop}
import it.unibo.demo.provider.{MqttProvider, TimedPose}
import it.unibo.demo.scenarios.*
import it.unibo.demo.{AllDemoToLoad, ID, Info, Position}
import it.unibo.mqtt.MqttContext
import it.unibo.utils.Position.given
import org.eclipse.paho.client.mqttv3.{MqttClient, MqttMessage}

import scala.concurrent.duration.*

/**
 * Drives the real loop - MQTT in, aggregate program, controller, MQTT out - against a fleet of
 * simulated robots, and checks that a formation actually forms.
 *
 * The unit suites pin down the control law; this one covers everything around it: pose decoding,
 * the neighbourhood and gradients, the publisher's rate and its stale-command watchdog, and the
 * wire format in both directions.
 *
 * It needs a broker, so it is skipped unless one is named:
 * {{{
 * docker run -d -p 1883:1883 eclipse-mosquitto:2.0
 * EMERGE_TEST_BROKER=tcp://localhost:1883 sbt test
 * }}}
 */
class AggregateLoopIntegrationSuite extends munit.FunSuite:

  override val munitTimeout: Duration = 3.minutes

  private val broker = sys.env.get("EMERGE_TEST_BROKER")
  private val leaderId = 0x000001
  private val fleet = List(leaderId, 0x000002, 0x000003, 0x000004, 0x000005)
  private val spacing = 0.4

  test("a line formation forms and settles"):
    assume(broker.isDefined, "set EMERGE_TEST_BROKER to run the integration suite")
    val (finalPoses, turning) = runFormation("lineShape", settleFor = 45.seconds)

    val positions = fleet.map(finalPoses)
    val (originX, originY) = positions.head
    // Fit a line through the fleet and measure how far off it anyone ended up.
    val centroidX = positions.map(_._1).sum / positions.size
    val centroidY = positions.map(_._2).sum / positions.size
    val axis = principalAxis(positions, centroidX, centroidY)
    val offsets = positions.map { case (x, y) =>
      math.abs((x - centroidX) * axis._2 - (y - centroidY) * axis._1)
    }
    val along = positions.map { case (x, y) => (x - centroidX) * axis._1 + (y - centroidY) * axis._2 }.sorted
    val gaps = along.sliding(2).map { case Seq(a, b) => b - a }.toList

    assert(offsets.max < 0.15, s"the fleet is not in a line: worst offset ${offsets.max}m, positions $positions")
    assert(gaps.forall(gap => gap > spacing * 0.5 && gap < spacing * 1.6),
      s"the spacing is wrong: $gaps (wanted about $spacing m), positions $positions")
    assert(math.hypot(positions.head._1 - originX, positions.head._2 - originY) < 1.0)

    // The complaint this change exists to fix: robots that turn and turn instead of driving. Taking
    // a slot needs about one turn, plus a little steering; anything near the limit below means the
    // fleet is circling its way there rather than going there.
    val worstTurning = math.toDegrees(turning.values.max)
    assert(worstTurning < 720.0, s"a robot turned ${worstTurning}deg to take its place in a line")

  test("a robot whose pose stops arriving is stopped, not left running"):
    assume(broker.isDefined, "set EMERGE_TEST_BROKER to run the integration suite")
    val url = broker.get
    val received = scala.collection.mutable.ListBuffer[String]()
    val listener = MqttClient(url, MqttClient.generateClientId())
    listener.connect()
    listener.subscribeWithResponse(f"/motors/${fleet(1)}%06X", (_: String, message: MqttMessage) => {
      received.synchronized(received += String(message.getPayload))
      ()
    })
    try
      // Drive the fleet, then let one robot go dark and watch what its motors are told.
      runFormation("lineShape", settleFor = 6.seconds, silenceAfter = Some((fleet(1), 3.seconds)))
      val tail = received.synchronized(received.toList).takeRight(10)
      assert(tail.nonEmpty, "no motor commands were seen at all")
      assert(tail.forall(_.contains("Stop")),
        s"a robot with no pose must be commanded to stop; last commands were $tail")
    finally
      listener.disconnect()
      listener.close()

  private def principalAxis(points: List[(Double, Double)], cx: Double, cy: Double): (Double, Double) =
    val sxx = points.map { case (x, _) => (x - cx) * (x - cx) }.sum
    val syy = points.map { case (_, y) => (y - cy) * (y - cy) }.sum
    val sxy = points.map { case (x, y) => (x - cx) * (y - cy) }.sum
    val angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    (math.cos(angle), math.sin(angle))

  /** Run the whole system for a while and report where the robots ended up. */
  private def runFormation(
      program: String,
      settleFor: FiniteDuration,
      silenceAfter: Option[(ID, FiniteDuration)] = None
  ): (Map[ID, (Double, Double)], Map[ID, Double]) =
    val url = broker.get
    val simulation = SimulatedFleet(url, fleet, silenceAfter)
    val defaults: Map[String, Any] = Map(
      "program" -> program,
      "leader" -> leaderId,
      "collisionArea" -> 0.3,
      "stabilityThreshold" -> 0.1
    ) ++ LineFormation.DEFAULTS ++ VFormation.DEFAULTS ++ VerticalLineFormation.DEFAULTS ++
      CircleFormation.DEFAULTS ++ SquareFormation.DEFAULTS ++ HeartFormation.DEFAULTS

    val run = for
      dispatcher <- Dispatcher.parallel[IO]
      mqttContext <- Resource.make(IO(MqttContext(url)))(context => IO {
        if context.client.isConnected then
          context.client.disconnect()
          context.client.close()
      }.handleErrorWith(_ => IO.unit))
      _ <- Resource.make(IO(simulation.start()))(_ => IO(simulation.stop()))
      configRef <- Resource.eval(Ref.of[IO, Map[String, Any]](defaults))
      worldRef <- Resource.eval(Ref.of[IO, Map[ID, TimedPose]](Map.empty))
      neighbourRef <- Resource.eval(Ref.of[IO, Map[ID, Set[ID]]](Map.empty))
    yield (dispatcher, mqttContext, configRef, worldRef, neighbourRef)

    run.use { case (dispatcher, mqttContext, configRef, worldRef, neighbourRef) =>
      given MqttContext = mqttContext
      given Dispatcher[IO] = dispatcher
      val provider = MqttProvider(configRef, worldRef, neighbourRef)
      val demos = AllDemoToLoad(
        "lineShape" -> LineFormation(),
        "circleShape" -> CircleFormation(),
        "stop" -> Stop()
      )
      val orchestrator = AggregateOrchestrator[Position, Actuation](demos)
      val render = new Boundary[ID, Position, Info]:
        override def output(environment: Environment[ID, Position, Info]): IO[Unit] = IO.unit
      for
        // The real configuration path, so the env-var overrides are exercised too. The simulated
        // robots keep the true physical config, so a wrong override shows up as bad behaviour.
        publisher <- MotorCommandPublisher(DriveConfig.fromEnvironment)
        update = RobotUpdateMqtt(publisher, DriveConfig.fromEnvironment)
        _ <- provider.start()
        _ <- publisher.run().background.use { _ =>
          UpdateLoop.loop(50)(provider, orchestrator, update, render).background.use { _ =>
            IO.sleep(settleFor)
          }
        }
      yield (simulation.poses, simulation.rotation)
    }.unsafeRunSync()

/**
 * A fleet of [[RobotPlant]] robots on the wire, standing in for `apps/robot-emulator`: it takes
 * `/motors/+` commands, integrates the same plant the unit tests use, and publishes `/pose/<id>`
 * and `/neighbors/<id>` the way the vision and neighbourhood services do.
 */
private class SimulatedFleet(
    url: String,
    ids: List[ID],
    silenceAfter: Option[(ID, FiniteDuration)]
):
  private val config = DriveConfig()
  private val stepMs = 5L
  private val poseIntervalMs = 50L
  private val plants = ids.zipWithIndex.map { case (id, index) =>
    // Start them scattered and pointing every which way, so nothing depends on a lucky pose.
    val angle = 2 * math.Pi * index / ids.size
    id -> RobotPlant(config, 0.6 * math.cos(angle), 0.6 * math.sin(angle), angle)
  }.toMap
  private val client = MqttClient(url, MqttClient.generateClientId())
  @volatile private var running = false
  private var worker: Thread = null

  private val turned = scala.collection.mutable.Map[ID, Double]().withDefaultValue(0.0)
  private val lastYaw = scala.collection.mutable.Map[ID, Double]() ++ plants.view.mapValues(_.markerYaw)

  def poses: Map[ID, (Double, Double)] = plants.view.mapValues(plant => (plant.x, plant.y)).toMap

  /** How far each robot has turned in total, in radians. Straight-line driving barely moves this. */
  def rotation: Map[ID, Double] = turned.synchronized(turned.toMap)

  def start(): Unit =
    client.connect()
    client.subscribeWithResponse("/motors/+", (topic: String, message: MqttMessage) => {
      val id = Integer.parseInt(topic.split("/").last, 16)
      plants.get(id).foreach { plant =>
        val payload = String(message.getPayload)
        plant.synchronized {
          if payload.contains("Stop") then plant.receiveStop()
          else
            val json = ujson.read(payload)("Move")
            plant.receiveMove(json("left").num, json("right").num)
        }
      }
    })
    running = true
    worker = Thread(() => loop(), "simulated-fleet")
    worker.setDaemon(true)
    worker.start()

  def stop(): Unit =
    running = false
    Option(worker).foreach(_.join(2000))
    if client.isConnected then client.disconnect()
    client.close()

  private def loop(): Unit =
    val startedAt = System.nanoTime()
    var nextPose = 0L
    while running do
      val elapsed = (System.nanoTime() - startedAt).nanos
      plants.foreach { case (id, plant) =>
        plant.synchronized(plant.integrate(stepMs / 1000.0))
        turned.synchronized {
          turned(id) += math.abs(DifferentialDrive.normalizeAngle(plant.markerYaw - lastYaw(id)))
          lastYaw(id) = plant.markerYaw
        }
      }
      if elapsed.toMillis >= nextPose then
        nextPose += poseIntervalMs
        publishPoses(elapsed)
      Thread.sleep(stepMs)

  private def publishPoses(elapsed: FiniteDuration): Unit =
    plants.foreach { case (id, plant) =>
      val muted = silenceAfter.exists { case (silent, after) => silent == id && elapsed >= after }
      if !muted then
        val payload = ujson.Obj(
          "x_m" -> plant.x,
          "y_m" -> plant.y,
          "heading_rad" -> plant.markerYaw,
          "speed_m_s" -> 0.0,
          "position_variance_m2" -> 0.0,
          "timestamp_us" -> 0
        )
        client.publish(f"/pose/$id%06X", ujson.write(payload).getBytes, 0, false)
        val neighbours = ids.filter(_ != id).map(other => f"$other%06X")
        client.publish(f"/neighbors/$id%06X", ujson.write(neighbours).getBytes, 0, false)
    }
