package it.unibo.demo.robot

import cats.effect.{IO, Ref}
import cats.syntax.all.*
import it.unibo.demo.ID
import it.unibo.mqtt.MqttContext

import scala.collection.concurrent.TrieMap
import scala.concurrent.duration.*

/** What the aggregate wants a robot's motors to be doing right now. */
enum MotorCommand:
  case Move(left: Double, right: Double)
  case Halt

private final case class Pending(
    command: MotorCommand,
    setAt: FiniteDuration,
    releasing: Boolean
)

/**
 * Owns every motor message the aggregate sends.
 *
 * Records desired commands and republishes them independently of the control loop. Frequent
 * publishing reduces firmware filter lag; shadow-state compensation largely cancels it. Stale
 * commands become `Stop` after [[commandTtl]], providing a faster fail-safe than the watchdog.
 */
class MotorCommandPublisher(
    private val pending: Ref[IO, Map[ID, Pending]],
    private val config: DriveConfig
)(using MqttContext):

  import MotorCommandPublisher.*

  /** The firmware's internal filter state, as far as we can tell. */
  private val firmwareState = TrieMap[ID, (Double, Double)]()

  /** Drive a robot. Refresh this at least every [[commandTtl]] or it decays to a stop. */
  def drive(id: ID, left: Double, right: Double): IO[Unit] =
    record(id, MotorCommand.Move(left, right), releasing = false)

  /** Bring a robot to a halt and keep it there. */
  def halt(id: ID): IO[Unit] = record(id, MotorCommand.Halt, releasing = false)

  /** Stop briefly, then return control to the dashboard. */
  def release(id: ID): IO[Unit] = record(id, MotorCommand.Halt, releasing = true)

  private def record(id: ID, command: MotorCommand, releasing: Boolean): IO[Unit] =
    IO.monotonic.flatMap { now =>
      pending.update { current =>
        val alreadyReleasing = releasing && current.get(id).exists(_.releasing)
        val since = if alreadyReleasing then current(id).setAt else now
        current + (id -> Pending(command, since, releasing))
      }
    }

  /** Expire stale commands and return the remaining commands. */
  def expireStale: IO[Map[ID, MotorCommand]] =
    for
      now <- IO.monotonic
      live <- pending.modify { current =>
        val next = current.flatMap { case (id, entry) =>
          val age = now - entry.setAt
          if entry.releasing then Option.when(age <= releaseGrace)(id -> entry)
          else if age > forgetAfter then None
          else if age > commandTtl then Some(id -> entry.copy(command = MotorCommand.Halt))
          else Some(id -> entry)
        }
        (next, next)
      }
      _ <- IO(firmwareState.keys.filterNot(live.contains).foreach(firmwareState.remove))
    yield live.view.mapValues(_.command).toMap

  /** Republish every live command, expiring the ones that have gone stale. */
  def publishPending: IO[Unit] =
    expireStale.flatMap(_.toList.traverse_ { case (id, command) => publish(id, command) })

  /** Run the publisher until cancelled. Meant to be started as a background fiber. */
  def run(period: FiniteDuration = defaultPeriod): IO[Unit] =
    (publishPending >> IO.sleep(period)).foreverM

  private def publish(id: ID, command: MotorCommand): IO[Unit] = IO {
    command match
      case MotorCommand.Halt =>
        // Stop resets the firmware filter.
        firmwareState.put(id, (0.0, 0.0))
        RobotMqttProtocol.stop(id)
      case MotorCommand.Move(left, right) =>
        val (shadowLeft, shadowRight) = firmwareState.getOrElse(id, (0.0, 0.0))
        val outLeft = compensate(shadowLeft, left)
        val outRight = compensate(shadowRight, right)
        firmwareState.put(id, (advance(shadowLeft, outLeft), advance(shadowRight, outRight)))
        RobotMqttProtocol.moveWith(id, outLeft, outRight)
  }

  private def compensate(shadow: Double, target: Double): Double =
    FirmwareLagCompensator.command(shadow, target, config.firmwareEmaAlpha)

  private def advance(shadow: Double, published: Double): Double =
    FirmwareLagCompensator.advance(shadow, published, config.firmwareEmaAlpha)

object MotorCommandPublisher:
  /** How often commands go on the wire. Fast enough to drive the firmware filter, slow enough that
    * the firmware's two-deep command channel (drained every 10 ms) never backs up. */
  val defaultPeriod: FiniteDuration = 33.millis

  /** A command not refreshed within this window is replaced by a stop. */
  val commandTtl: FiniteDuration = 400.millis

  /** How long a stopped-but-not-refreshed robot keeps being told to stop before we forget it. */
  val forgetAfter: FiniteDuration = 2.seconds

  /** How long a released robot is told to stop before control is handed back. */
  val releaseGrace: FiniteDuration = 300.millis

  def apply(config: DriveConfig)(using MqttContext): IO[MotorCommandPublisher] =
    Ref.of[IO, Map[ID, Pending]](Map.empty).map(new MotorCommandPublisher(_, config))

/** Compensates for the firmware's command smoothing. */
object FirmwareLagCompensator:

  /** The command to publish so the firmware's filtered output reaches `target`, as fast as it can. */
  def command(shadow: Double, target: Double, alpha: Double): Double =
    if alpha >= 1.0 then target
    else math.max(-1.0, math.min(1.0, shadow + (target - shadow) / alpha))

  /** Apply the firmware's filter, to keep the shadow in step with what it just received. */
  def advance(shadow: Double, published: Double, alpha: Double): Double =
    shadow + alpha * (published - shadow)
