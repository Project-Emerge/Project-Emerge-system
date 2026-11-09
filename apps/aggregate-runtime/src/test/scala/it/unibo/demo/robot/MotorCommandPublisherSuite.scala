package it.unibo.demo.robot

import cats.effect.unsafe.implicits.global
import cats.effect.{IO, Ref}
import cats.syntax.all.*
import it.unibo.demo.ID
import it.unibo.mqtt.MqttContext

import scala.concurrent.duration.*

class MotorCommandPublisherSuite extends munit.FunSuite:

  /**
   * Exercises the publisher's bookkeeping without touching a broker - nothing here publishes, so
   * the missing MQTT context is never used. The wire side is covered by the integration suite.
   */
  private def afterExpiry(actions: MotorCommandPublisher => IO[Unit]): Map[ID, MotorCommand] =
    given MqttContext = null
    (for
      ref <- Ref.of[IO, Map[ID, Pending]](Map.empty)
      publisher = new MotorCommandPublisher(ref, DriveConfig())
      _ <- actions(publisher)
      live <- publisher.expireStale
    yield live).unsafeRunSync()

  test("a released robot is let go, however often the program repeats itself"):
    // `NoOp` means "someone else is driving this one". The program says so on every tick, so timing
    // the release from the most recent tick would hold the robot under a stop that never lifts.
    val live = afterExpiry { publisher =>
      List.fill(20)(publisher.release(1) >> IO.sleep(30.millis)).sequence_
    }
    assert(live.isEmpty, s"the robot should have been handed back by now, but is still $live")

  test("a robot that is merely stopped stays stopped"):
    val live = afterExpiry(publisher => publisher.halt(1) >> IO.sleep(100.millis))
    assertEquals(live, Map(1 -> MotorCommand.Halt))

  test("a command that stops being refreshed decays into a stop"):
    // The robot's own watchdog is three seconds; a robot whose pose has dropped out must not be
    // left running for anything like that long on the strength of its last command.
    val live = afterExpiry { publisher =>
      publisher.drive(1, 0.5, 0.5) >> IO.sleep(MotorCommandPublisher.commandTtl + 100.millis)
    }
    assertEquals(live, Map(1 -> MotorCommand.Halt), "a robot must not keep running on a stale command")

  test("a freshly refreshed command is left alone"):
    val live = afterExpiry(publisher => publisher.drive(1, 0.5, -0.2))
    assertEquals(live, Map(1 -> MotorCommand.Move(0.5, -0.2)))
