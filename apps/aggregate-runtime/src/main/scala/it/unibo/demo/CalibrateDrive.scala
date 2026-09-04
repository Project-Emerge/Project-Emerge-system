package it.unibo.demo

import cats.effect.std.Dispatcher
import cats.effect.{IO, IOApp, Ref, Resource}
import it.unibo.demo.provider.MqttProtocol
import it.unibo.demo.robot.{DifferentialDrive, DriveConfig, RobotMqttProtocol}
import it.unibo.mqtt.MqttContext
import org.eclipse.paho.client.mqttv3.MqttMessage
import upickle.default.read

import scala.concurrent.duration.*

/**
 * Measures the two conventions that relate what the cameras see to how the robot is built, so they
 * stop being assumptions.
 *
 * Neither can be worked out from the source. `markerYawOffsetRad` depends on which way the ArUco
 * sticker was glued on, and `invertWheels` on how the motors were wired: the firmware does not swap
 * them, but the emulator does, and only one of those can match the robot in front of you. Get
 * either wrong and the robot turns away from where it is going rather than towards it - it may
 * still get there, by coming all the way around, which is precisely why this is worth measuring
 * rather than eyeballing.
 *
 * Run it with one robot on a clear patch of floor, at least half a metre from anything:
 *
 * {{{
 * MQTT_URL=tcp://localhost:1883 sbt "runMain it.unibo.demo.CalibrateDrive <6-hex-device-id>"
 * }}}
 *
 * Then put the values it prints into `DRIVE_MARKER_YAW_OFFSET_DEG` and `DRIVE_INVERT_WHEELS`.
 */
object CalibrateDrive extends IOApp.Simple:

  private val brokerUrl = System.getenv().getOrDefault("MQTT_URL", "tcp://localhost:1883")
  private val driveSpeed = 0.5
  private val settleTime = 700.millis

  private final case class Pose(x: Double, y: Double, markerYaw: Double)

  override def run: IO[Unit] =
    IO(Option(System.getProperty("calibrate.robot")).orElse(sys.env.get("CALIBRATE_ROBOT"))).flatMap {
      case None => IO.println(usage)
      case Some(deviceId) => calibrate(deviceId)
    }

  private val usage =
    """Which robot? Pass its 6-hex device id, e.g.
      |  CALIBRATE_ROBOT=A1B2C3 sbt "runMain it.unibo.demo.CalibrateDrive"
      |Give it a clear half metre of floor in every direction before starting.""".stripMargin

  private def calibrate(deviceId: String): IO[Unit] =
    val robotId = Integer.parseInt(deviceId, 16)
    resources(deviceId).use { case (mqttContext, latest) =>
      given MqttContext = mqttContext
      for
        _ <- IO.println(s"Calibrating robot $deviceId against broker $brokerUrl")
        _ <- waitForPose(latest)
        offset <- measureMarkerOffset(robotId, latest)
        inverted <- measureWheelOrder(robotId, latest)
        _ <- IO(RobotMqttProtocol.stop(robotId))
        _ <- report(offset, inverted)
      yield ()
    }

  /**
   * Drive straight and compare the direction the robot actually travelled with the yaw the cameras
   * report. The difference is the rotation from the marker frame to the robot's forward axis.
   */
  private def measureMarkerOffset(robotId: Int, latest: Ref[IO, Option[Pose]])(using MqttContext): IO[Double] =
    for
      _ <- IO.println("\n1/2  Driving forward to find which way the marker faces...")
      before <- currentPose(latest)
      _ <- drive(robotId, driveSpeed, driveSpeed, 1500.millis)
      after <- currentPose(latest)
      travelled = math.hypot(after.x - before.x, after.y - before.y)
      _ <- IO.raiseWhen(travelled < 0.05)(
        IllegalStateException(f"the robot only moved ${travelled}%.3f m; check it is powered, unobstructed and visible")
      )
      travelDirection = math.atan2(after.y - before.y, after.x - before.x)
      // The yaw drifts a little over the run, so compare against the average of the two readings.
      averageYaw = before.markerYaw + DifferentialDrive.normalizeAngle(after.markerYaw - before.markerYaw) / 2
      offset = DifferentialDrive.normalizeAngle(travelDirection - averageYaw)
      _ <- IO.println(f"     travelled ${travelled}%.3f m towards ${math.toDegrees(travelDirection)}%.1f deg, " +
        f"marker yaw ${math.toDegrees(averageYaw)}%.1f deg")
    yield offset

  /**
   * Spin with the left wheel back and the right wheel forward. In a right-handed world frame that
   * is a counter-clockwise turn, so the reported yaw should increase. If it decreases, the wheels
   * are the other way round from what the code assumes.
   */
  private def measureWheelOrder(robotId: Int, latest: Ref[IO, Option[Pose]])(using MqttContext): IO[Boolean] =
    for
      _ <- IO.println("\n2/2  Spinning to find which wheel is which...")
      before <- currentPose(latest)
      _ <- drive(robotId, -driveSpeed, driveSpeed, 1200.millis)
      after <- currentPose(latest)
      turned = DifferentialDrive.normalizeAngle(after.markerYaw - before.markerYaw)
      _ <- IO.raiseWhen(math.abs(turned) < math.toRadians(15.0))(
        IllegalStateException(f"the robot only turned ${math.toDegrees(turned)}%.1f deg; too little to tell")
      )
      _ <- IO.println(f"     turned ${math.toDegrees(turned)}%.1f deg")
    yield turned < 0

  private def report(offsetRad: Double, inverted: Boolean): IO[Unit] =
    val degrees = math.toDegrees(offsetRad)
    val rounded = math.round(degrees / 90.0) * 90
    val current = DriveConfig.fromEnvironment
    for
      _ <- IO.println("\n" + "-" * 72)
      _ <- IO.println(f"  measured marker yaw offset : ${degrees}%.1f deg (nearest quarter turn: $rounded deg)")
      _ <- IO.println(s"  measured wheel order       : ${if inverted then "swapped" else "as published"}")
      _ <- IO.println("\n  Set these before starting the aggregate runtime:")
      _ <- IO.println(s"    DRIVE_MARKER_YAW_OFFSET_DEG=$rounded")
      _ <- IO.println(s"    DRIVE_INVERT_WHEELS=$inverted")
      _ <- IO.println(f"\n  Currently in effect: ${math.toDegrees(current.markerYawOffsetRad)}%.0f deg, " +
        s"invertWheels=${current.invertWheels}")
      _ <- IO.println(
        if math.abs(DifferentialDrive.normalizeAngle(offsetRad - current.markerYawOffsetRad)) > math.toRadians(20.0) ||
          inverted != current.invertWheels
        then "  >> This does NOT match the running configuration. That mismatch alone is enough to\n" +
          "     make the robots turn away from their goals instead of towards them."
        else "  >> This matches the running configuration."
      )
      _ <- IO.println("-" * 72)
    yield ()

  private def drive(robotId: Int, left: Double, right: Double, duration: FiniteDuration)(using MqttContext): IO[Unit] =
    // Republished throughout: the firmware smooths each command it receives, so a single message
    // would spend the whole run ramping up instead of moving at the speed asked for.
    val publish = IO(RobotMqttProtocol.moveWith(robotId, left, right)) >> IO.sleep(20.millis)
    publish.foreverM.timeoutTo(duration, IO.unit) >>
      IO(RobotMqttProtocol.stop(robotId)) >> IO.sleep(settleTime)

  private def currentPose(latest: Ref[IO, Option[Pose]]): IO[Pose] =
    latest.get.flatMap(IO.fromOption(_)(IllegalStateException("no pose received; is the vision system running?")))

  private def waitForPose(latest: Ref[IO, Option[Pose]]): IO[Unit] =
    val poll = latest.get.map(_.isDefined).flatMap(IO.raiseUnless(_)(IllegalStateException("waiting")))
    poll.handleErrorWith(_ => IO.sleep(200.millis) >> poll).timeoutTo(
      10.seconds,
      IO.raiseError(IllegalStateException("no pose arrived on /pose/+ within 10s; is the vision system running?"))
    )

  private def resources(deviceId: String): Resource[IO, (MqttContext, Ref[IO, Option[Pose]])] =
    for
      dispatcher <- Dispatcher.parallel[IO]
      mqttContext <- Resource.make(IO(MqttContext(brokerUrl)))(context => IO {
        if context.client.isConnected then
          context.client.disconnect()
          context.client.close()
      }.handleErrorWith(_ => IO.unit))
      latest <- Resource.eval(Ref.of[IO, Option[Pose]](None))
      _ <- Resource.eval(IO {
        val client = mqttContext.client
        if !client.isConnected then client.connect()
        client.subscribeWithResponse(s"/pose/$deviceId", (_: String, message: MqttMessage) => {
          dispatcher.unsafeRunAndForget {
            IO(read[MqttProtocol.RobotPosition](message.getPayload))
              .flatMap(pose => latest.set(Some(Pose(pose.x_m, pose.y_m, pose.heading_rad))))
              .handleErrorWith(_ => IO.unit)
          }
        })
      })
    yield (mqttContext, latest)
