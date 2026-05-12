package it.unibo.demo.manager

import io.javalin.Javalin
import io.javalin.websocket.WsContext
import it.unibo.core.EnvironmentProvider
import it.unibo.demo.ID
import org.slf4j.LoggerFactory
import upickle.default.{Reader, macroR, read, reader, write}

import scala.concurrent.ExecutionContext
import scala.util.{Failure, Success, Try}

object OffloadingManagerProtocol:
  val JsonRpcVersion = "2.0"
  val ChangeStatusMethod = "change_status"
  val DefaultPath = "/ws/aggregate"

  final case class ChangeState(id: ID, calc: Boolean)
  final case class JsonRpcNotification[Params](jsonrpc: String, method: String, params: Params)
  final case class ErrorResponse(error: String)

  given Reader[ID] = reader[ujson.Value].map:
    case value if value.numOpt.nonEmpty => value.num.toInt
    case value if value.strOpt.exists(_.toIntOption.nonEmpty) => value.str.toInt
    case _ => throw IllegalArgumentException("change_status params must include a numeric robot id")

  given Reader[ChangeState] = macroR
  given [Params: Reader]: Reader[JsonRpcNotification[Params]] = macroR
  given upickle.default.Writer[ErrorResponse] = upickle.default.macroW

final class OffloadingManagerWebSocketServer(
    provider: EnvironmentProvider[ID, ?, ?, ?],
    host: String,
    port: Int,
    path: String
)(using ExecutionContext):
  import OffloadingManagerProtocol.*

  private val logger = LoggerFactory.getLogger(classOf[OffloadingManagerWebSocketServer])

  private val app = Javalin.create: config =>
    config.showJavalinBanner = false

  app.ws(path, ws =>
    ws.onConnect: ctx =>
      logger.info(s"Offloading manager connected to aggregate runtime from ${remoteAddress(ctx)}")
    ws.onMessage: ctx =>
      handleMessage(ctx, ctx.message())
    ws.onClose: ctx =>
      logger.info(s"Offloading manager disconnected from aggregate runtime: ${ctx.reason()}")
    ws.onError: ctx =>
      logger.warn("WebSocket error on aggregate runtime server", ctx.error())
  )

  def start(): Unit =
    logger.info(s"Starting aggregate runtime WebSocket server on ws://$host:$port$path")
    app.start(host, port)

  def stop(): Unit =
    app.stop()

  private def handleMessage(ctx: WsContext, message: String): Unit =
    Try(read[JsonRpcNotification[ChangeState]](message)) match
      case Failure(error) =>
        logger.warn(s"Received invalid WebSocket payload: $message", error)
        ctx.send(write(ErrorResponse(error.getMessage)))
      case Success(notification) if isChangeStatus(notification) =>
        updateRobotStatus(notification.params)
      case Success(_) =>
        logger.debug(s"Ignoring unsupported WebSocket message: $message")

  private def isChangeStatus(notification: JsonRpcNotification[ChangeState]): Boolean =
    notification.jsonrpc == JsonRpcVersion && notification.method == ChangeStatusMethod

  private def updateRobotStatus(change: ChangeState): Unit =
    val update = if change.calc then provider.enableRobot(change.id) else provider.disableRobot(change.id)
    update.onComplete:
      case Success(_) if change.calc =>
        logger.info(s"Enabled aggregate computation for robot ${change.id}")
      case Success(_) =>
        logger.info(s"Disabled aggregate computation for robot ${change.id}")
      case Failure(error) =>
        logger.warn(s"Cannot update aggregate computation state for robot ${change.id}", error)

  private def remoteAddress(ctx: WsContext): String =
    Option(ctx.session.getRemoteAddress)
      .map(_.toString)
      .getOrElse("unknown")

object OffloadingManagerWebSocketServer:
  private val OffloadingManagerWsHostEnv = "OFFLOADING_MANAGER_WS_HOST"
  private val OffloadingManagerWsPortEnv = "OFFLOADING_MANAGER_WS_PORT"
  private val DefaultHost = "0.0.0.0"
  private val DefaultPort = 8080

  def fromEnvironment(provider: EnvironmentProvider[ID, ?, ?, ?])(using
      ExecutionContext
  ): OffloadingManagerWebSocketServer =
    val host = Option(System.getenv(OffloadingManagerWsHostEnv)).map(_.trim).filter(_.nonEmpty).getOrElse(DefaultHost)
    val port = Option(System.getenv(OffloadingManagerWsPortEnv)).flatMap(_.toIntOption).getOrElse(DefaultPort)
    OffloadingManagerWebSocketServer(provider, host, port, OffloadingManagerProtocol.DefaultPath)