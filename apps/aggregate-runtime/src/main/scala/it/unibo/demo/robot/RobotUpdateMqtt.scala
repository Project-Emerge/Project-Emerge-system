package it.unibo.demo.robot

import cats.effect.IO
import it.unibo.core.{Environment, EnvironmentUpdate}
import it.unibo.demo.robot.Actuation.{Forward, NoOp, Rotation, Stop}
import it.unibo.demo.{ID, Info, Position}
import org.slf4j.LoggerFactory

import scala.collection.concurrent.TrieMap
import scala.concurrent.duration.*

enum Actuation:
  case Rotation(rotationVector: (Double, Double))
  case Forward(direction: (Double, Double), distanceToGoal: Double)
  case NoOp
  case Stop

/**
 * Turns the aggregate program's output into motor commands.
 *
 * This is only glue: the kinematics live in [[DifferentialDrive]], the control law in
 * [[HeadingController]], and the publishing and fail-safes in [[MotorCommandPublisher]].
 */
class RobotUpdateMqtt(
    private val publisher: MotorCommandPublisher,
    private val config: DriveConfig = DriveConfig.fromEnvironment,
    private val gains: ControlGains = ControlGains()
) extends EnvironmentUpdate[ID, Position, Actuation, Info, Environment[ID, Position, Info]]:

  import RobotUpdateMqtt.*

  private val logger = LoggerFactory.getLogger(classOf[RobotUpdateMqtt])
  private val controls = TrieMap[ID, RobotControl]()
  private val orientationWarned = TrieMap[ID, Unit]()

  override def update(world: Environment[ID, Position, Info], id: ID, actuation: Actuation): IO[Unit] =
    actuation match
      case _ if !world.nodes.contains(id) => forget(id) *> publisher.halt(id)
      case NoOp => forget(id) *> publisher.release(id)
      case Stop => forget(id) *> publisher.halt(id)
      case Rotation(target) => drive(world, id, target, distanceToGoal = 0.0, translate = false)
      case Forward(direction, distanceToGoal) => drive(world, id, direction, distanceToGoal, translate = true)

  private def forget(id: ID): IO[Unit] = IO(controls.remove(id)).void

  private def drive(
      world: Environment[ID, Position, Info],
      id: ID,
      target: (Double, Double),
      distanceToGoal: Double,
      translate: Boolean
  ): IO[Unit] =
    orientationOf(world, id) match
      case None =>
        // Better to stand still than to drive confidently in a direction derived from a guess.
        warnOnce(id, s"robot $id has no usable orientation; holding position")
        forget(id) *> publisher.halt(id)
      case Some(_) if isDegenerate(target) =>
        forget(id) *> publisher.halt(id)
      case Some(markerYaw) =>
        orientationWarned.remove(id)
        for
          now <- IO.monotonic
          twist <- IO(advance(id, now, markerYaw, target, distanceToGoal, translate))
          _ <- command(id, twist)
        yield ()

  private def advance(
      id: ID,
      now: FiniteDuration,
      markerYaw: Double,
      target: (Double, Double),
      distanceToGoal: Double,
      translate: Boolean
  ): Twist =
    val previous = controls.get(id)
    val elapsed = previous.map(control => now - control.at)
    // A robot that has been out of sight for a while is a fresh start, not a huge time step.
    val (state, dt) = elapsed match
      case Some(gap) if gap >= minimumStep && gap <= maximumStep => (previous.get.state, gap.toNanos / 1e9)
      case _ => (ControlState.initial, MotorCommandPublisher.defaultPeriod.toNanos / 1e9)

    val heading = DifferentialDrive.bodyHeading(markerYaw, config)
    val targetAngle = math.atan2(target._2, target._1)
    val (twist, next) = HeadingController.step(
      state = state,
      dtSeconds = dt,
      heading = heading,
      targetAngle = targetAngle,
      distanceToGoal = distanceToGoal,
      translate = translate,
      allowReverse = translate,
      config = config,
      gains = gains
    )
    controls.put(id, RobotControl(next, now))
    twist

  private def command(id: ID, twist: Twist): IO[Unit] =
    if twist.isStill then publisher.halt(id)
    else
      val (left, right) = DifferentialDrive.toWheels(twist.linearMs, twist.angularRadS, config)
      if left == 0.0 && right == 0.0 then publisher.halt(id) else publisher.drive(id, left, right)

  private def orientationOf(world: Environment[ID, Position, Info], id: ID): Option[Double] =
    val raw = world.sensing(id).get("orientation").flatMap {
      case value: Double => Some(value)
      case value: java.lang.Double => Some(value.doubleValue())
      case value: Float => Some(value.toDouble)
      case value: String => value.toDoubleOption
      case _ => None
    }
    raw.filterNot(value => value.isNaN || value.isInfinite)

  private def isDegenerate(vector: (Double, Double)): Boolean =
    vector._1.isNaN || vector._2.isNaN ||
      (math.abs(vector._1) < 1e-9 && math.abs(vector._2) < 1e-9)

  private def warnOnce(id: ID, message: String): Unit =
    if orientationWarned.putIfAbsent(id, ()).isEmpty then logger.warn(message)

object RobotUpdateMqtt:
  private final case class RobotControl(state: ControlState, at: FiniteDuration)

  /** Below this the measured step is noise; above it the controller starts over. */
  private val minimumStep: FiniteDuration = 5.millis
  private val maximumStep: FiniteDuration = 250.millis
