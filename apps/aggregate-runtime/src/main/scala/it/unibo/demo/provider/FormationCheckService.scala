package it.unibo.demo.provider

import cats.effect.IO
import cats.effect.std.Dispatcher
import it.unibo.demo.formation.FormationCompiler
import it.unibo.mqtt.MqttContext
import org.eclipse.paho.client.mqttv3.MqttMessage
import org.slf4j.LoggerFactory

import java.nio.charset.StandardCharsets

/**
 * Answers `/formation/compile` requests on `/formation/compile/<id>`, never retained.
 *
 * The work runs on the blocking pool, off the MQTT callback thread: a cold compile takes over a
 * second, and pose delivery must not wait behind it.
 */
final class FormationCheckService(compiler: FormationCompiler)(using dispatcher: Dispatcher[IO], mqttContext: MqttContext):

  private val logger = LoggerFactory.getLogger(classOf[FormationCheckService])

  /** Subscribes on the client [[MqttProvider.start]] has already connected. */
  def start(): IO[Unit] = IO {
    mqttContext.client.subscribeWithResponse(
      FormationCheck.RequestTopic,
      (_: String, message: MqttMessage) => dispatcher.unsafeRunAndForget(answer(message.getPayload))
    )
  }

  private def answer(payload: Array[Byte]): IO[Unit] =
    IO(ujson.read(payload)).attempt
      .map(_.left.map(_.getMessage).flatMap(FormationCheck.parse))
      .flatMap {
        case Left(reason) => IO(logger.warn(s"Ignored a malformed compile request: $reason"))
        case Right(request) =>
          for
            result <- IO.blocking(FormationCheck.run(compiler, request))
            body = FormationCheck.encode(result).render().getBytes(StandardCharsets.UTF_8)
            _ <- IO.blocking(mqttContext.client.publish(FormationCheck.responseTopic(request.id), body, 1, false))
          yield ()
      }
      .handleErrorWith(error => IO(logger.warn("Could not answer a compile request", error)))
