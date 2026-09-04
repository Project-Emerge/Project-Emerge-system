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
 * The control loop only records what it wants; this publisher decides when it goes on the wire, at
 * a rate decoupled from the loop. It exists for three reasons:
 *
 *  1. **It shortens the firmware's actuator lag.** The firmware smooths incoming commands with an
 *     EMA applied once per *arrival*, so its time constant is roughly `10 / publishRate`. At the
 *     old 5 Hz control rate that was about two seconds of lag inside a feedback loop. Republishing
 *     at 30 Hz brings it down to a third of a second.
 *  2. **It cancels most of what remains.** The filter is deterministic, so the publisher tracks a
 *     shadow copy of the firmware's internal state and sends the value that drives the *filtered*
 *     output to the requested one. A lost packet only makes the shadow briefly optimistic; the
 *     filter is contracting, so it re-converges on its own.
 *  3. **It fails safe.** A command that stops being refreshed decays to `Stop` after
 *     [[commandTtl]], long before the firmware's own three-second watchdog. A robot whose pose
 *     drops out therefore stops, instead of holding its last command - a full-speed spin included.
 */
class MotorCommandPublisher(
    private val pending: Ref[IO, Map[ID, Pending]],
    private val config: DriveConfig
)(using MqttContext):

  import MotorCommandPublisher.*

  /** The firmware's internal filter state, as far as we can tell. Only the publisher fiber touches it. */
  private val firmwareState = TrieMap[ID, (Double, Double)]()

  /** Drive a robot. Refresh this at least every [[commandTtl]] or it decays to a stop. */
  def drive(id: ID, left: Double, right: Double): IO[Unit] =
    record(id, MotorCommand.Move(left, right), releasing = false)

  /** Bring a robot to a halt and keep it there. */
  def halt(id: ID): IO[Unit] = record(id, MotorCommand.Halt, releasing = false)

  /**
   * Give up control of a robot, e.g. because the program returned `NoOp` and someone is driving it
   * from the dashboard. It is stopped first, briefly, so it cannot coast away on a stale command,
   * and then left alone.
   */
  def release(id: ID): IO[Unit] = record(id, MotorCommand.Halt, releasing = true)

  private def record(id: ID, command: MotorCommand, releasing: Boolean): IO[Unit] =
    IO.monotonic.flatMap { now =>
      pending.update { current =>
        // A release is timed from when control was given up, not from the last time the program
        // said so. The program repeats `NoOp` every tick, so refreshing the timestamp here would
        // keep the robot pinned under a stop it can never be let out of.
        val alreadyReleasing = releasing && current.get(id).exists(_.releasing)
        val since = if alreadyReleasing then current(id).setAt else now
        current + (id -> Pending(command, since, releasing))
      }
    }

  /**
   * Age the recorded commands: turn ones that have stopped being refreshed into stops, drop the
   * robots that have been handed back or have been stopped long enough, and report what is left.
   */
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
        // The firmware's `Stop` calls `stop()`, which zeroes its filter outright instead of easing
        // into it, so the shadow can follow it exactly.
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

/**
 * Cancels the firmware's command smoothing.
 *
 * The firmware applies `y <- y + alpha * (u - y)` to every command it receives, so a step change
 * takes roughly `10 / alpha` messages to arrive. Because that recurrence is deterministic, a sender
 * that tracks `y` can solve it for the `u` that puts `y` exactly on target next message, and the
 * clamp means an unreachable step is simply approached as fast as the actuator allows instead of
 * being drawn out over a second.
 */
object FirmwareLagCompensator:

  /** The command to publish so the firmware's filtered output reaches `target`, as fast as it can. */
  def command(shadow: Double, target: Double, alpha: Double): Double =
    if alpha >= 1.0 then target
    else math.max(-1.0, math.min(1.0, shadow + (target - shadow) / alpha))

  /** Apply the firmware's filter, to keep the shadow in step with what it just received. */
  def advance(shadow: Double, published: Double, alpha: Double): Double =
    shadow + alpha * (published - shadow)
