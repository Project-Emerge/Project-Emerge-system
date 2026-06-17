package it.unibo.demo.manager

import it.unibo.core.EnvironmentProvider
import it.unibo.demo.ID

import java.net.URI
import scala.concurrent.ExecutionContext
import scala.concurrent.duration.*
import scala.util.{Failure, Success, Try}

object OffloadingManagerProtocol:
  val ChangeStatusMethod = "change_status"
  val DefaultPath = "/ws/aggregate"

  final case class ChangeState(id: ID, calc: Boolean)

/** WebSocket client that connects to the Offloading Manager's `/ws/aggregate` endpoint.
  *
  * The Offloading Manager is the WebSocket server: it owns the connection lifecycle and pushes `change_status`
  * JSON-RPC notifications to tell the aggregate runtime which robots should (de)activate the aggregate computation.
  * This client dials the manager, listens for those notifications and forwards them to the [[EnvironmentProvider]].
  */
final class OffloadingManagerWebSocketClient(
    provider: EnvironmentProvider[ID, ?, ?, ?],
    uri: URI,
    reconnectDelay: FiniteDuration
)(using ExecutionContext) extends BaseWebSocketClient(uri, reconnectDelay, "offloading-manager-ws-reconnect"):
  import OffloadingManagerProtocol.*

  override protected def handleMessage(message: String): Unit =
    Try(parseChangeStatus(message)) match
      case Success(Some(change)) => updateRobotStatus(change)
      case Success(None)         => logger.debug(s"Ignoring unsupported WebSocket message: $message")
      case Failure(error)        => logger.warn(s"Received invalid WebSocket payload: $message", error)

  /** Parses a JSON-RPC `change_status` notification, e.g.
    * `{ "jsonrpc": 2.0, "method": "change_status", "params": { "id": 1, "calc": true } }`.
    * The `jsonrpc` field is intentionally not validated as the manager encodes it as a number.
    */
  private def parseChangeStatus(message: String): Option[ChangeState] =
    val json = ujson.read(message)
    val method = json.obj.get("method").flatMap(_.strOpt)
    Option.when(method.contains(ChangeStatusMethod)):
      val params = json("params")
      ChangeState(robotId(params("id")), params("calc").bool)

  private def robotId(value: ujson.Value): ID =
    value.numOpt.map(_.toInt)
      .orElse(value.strOpt.flatMap(_.toIntOption))
      .getOrElse(throw IllegalArgumentException("change_status params must include a numeric robot id"))

  private def updateRobotStatus(change: ChangeState): Unit =
    val update = if change.calc then provider.enableRobot(change.id) else provider.disableRobot(change.id)
    update.onComplete:
      case Success(_) if change.calc =>
        logger.info(s"Enabled aggregate computation for robot ${change.id}")
      case Success(_) =>
        logger.info(s"Disabled aggregate computation for robot ${change.id}")
      case Failure(error) =>
        logger.warn(s"Cannot update aggregate computation state for robot ${change.id}", error)

object OffloadingManagerWebSocketClient:
  import BaseWebSocketClient.*

  private val PathEnv = "OFFLOADING_MANAGER_WS_PATH"

  def fromEnvironment(provider: EnvironmentProvider[ID, ?, ?, ?])(using
      ExecutionContext
  ): OffloadingManagerWebSocketClient =
    val host = env(HostEnv).getOrElse(DefaultHost)
    val port = env(PortEnv).flatMap(_.toIntOption).getOrElse(DefaultPort)
    val path = env(PathEnv).getOrElse(OffloadingManagerProtocol.DefaultPath)
    val reconnect = env(ReconnectSecondsEnv).flatMap(_.toLongOption).map(_.seconds).getOrElse(DefaultReconnect)
    val uri = URI.create(s"ws://$host:$port$path")
    OffloadingManagerWebSocketClient(provider, uri, reconnect)
