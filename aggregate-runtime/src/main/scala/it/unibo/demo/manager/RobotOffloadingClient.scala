package it.unibo.demo.manager

import it.unibo.demo.ID

import java.net.URI
import java.net.http.WebSocket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import scala.concurrent.duration.*
import scala.util.{Failure, Success, Try}

/** A robot-side offloading client: connects to the offloading-manager `/ws/robots/{id}` endpoint,
  * simulating a robot that takes part in the offloading protocol (JSON-RPC 2.0).
  *
  *   - On connection it sends a self `change_status` request asking to offload its aggregate
  *     computation to the central runtime.
  *   - It answers manager-initiated `change_status` requests (accept), e.g. when the offloading
  *     state is changed from the dashboard / decision module.
  *
  * Whenever the aggregate-offloading state for this robot changes, [[onOffloadChange]] is invoked
  * with the new flag (`true` = offloaded to the central runtime, `false` = computed locally).
  */
final class RobotOffloadingClient(
    robotId: ID,
    uri: URI,
    reconnectDelay: FiniteDuration,
    onOffloadChange: Boolean => Unit
) extends BaseWebSocketClient(uri, reconnectDelay, s"robot-$robotId-offloading-reconnect"):

  private val nextRequestId = new AtomicInteger(0)
  private val pendingRequests = new ConcurrentHashMap[Int, Boolean]() // request id -> requested aggregate flag

  override protected def logPrefix: String = s"Robot $robotId "

  override protected def onConnect(webSocket: WebSocket): Unit =
    requestOffload(aggregate = true) // on startup, ask to offload this robot

  /** Send a self `change_status` request for this robot (true = ask to offload aggregate to the central runtime). */
  def requestOffload(aggregate: Boolean): Unit =
    Option(socketRef.get()).foreach: socket =>
      val id = nextRequestId.getAndIncrement()
      pendingRequests.put(id, aggregate)
      val payload = ujson.Obj(
        "jsonrpc" -> ujson.Num(2.0),
        "method" -> ujson.Str("change_status"),
        "params" -> ujson.Obj("id" -> ujson.Num(robotId.toDouble), "type" -> offloadingType(aggregate)),
        "id" -> ujson.Num(id.toDouble)
      )
      socket.sendText(ujson.write(payload), true)
      logger.info(s"Robot $robotId requesting offload(aggregate=$aggregate) id=$id")

  private def offloadingType(aggregate: Boolean): ujson.Obj =
    ujson.Obj("aggregate" -> ujson.Bool(aggregate), "aruco" -> ujson.Bool(false), "neighbor" -> ujson.Bool(false))

  override protected def handleMessage(message: String): Unit =
    Try(ujson.read(message)) match
      case Failure(error) => logger.warn(s"Robot $robotId received invalid payload: $message", error)
      case Success(json) =>
        val obj = json.obj
        if obj.contains("method") && obj.contains("id") then handleManagerRequest(obj)
        else if obj.contains("result") then handleResponse(obj)
        else if obj.contains("error") then logger.warn(s"Robot $robotId received error: ${obj("error")}")

  /** Manager -> robot `change_status` request: adopt the requested state and accept it. */
  private def handleManagerRequest(obj: ujson.Obj): Unit =
    val msgId = obj("id").num.toInt
    val aggregate = Try(obj("params")("type")("aggregate").bool).getOrElse(false)
    logger.info(s"Robot $robotId received offloading change request: aggregate=$aggregate (id=$msgId)")
    onOffloadChange(aggregate)
    respond(msgId)

  /** Response to one of our self requests. */
  private def handleResponse(obj: ujson.Obj): Unit =
    val msgId = obj("id").num.toInt
    val success = obj("result") match
      case ujson.Bool(b) => b
      case o: ujson.Obj  => Try(o("success").bool).getOrElse(true)
      case _             => true
    Option(pendingRequests.remove(msgId)).foreach: requestedAggregate =>
      if success then
        logger.info(s"Robot $robotId offload request id=$msgId accepted (aggregate=$requestedAggregate)")
        onOffloadChange(requestedAggregate)
      else
        logger.info(s"Robot $robotId offload request id=$msgId rejected")

  /** Reply to a manager request. The manager validates the result as { success: bool }. */
  private def respond(msgId: Int): Unit =
    Option(socketRef.get()).foreach: socket =>
      val payload = ujson.Obj(
        "jsonrpc" -> ujson.Num(2.0),
        "result" -> ujson.Obj("success" -> ujson.Bool(true)),
        "id" -> ujson.Num(msgId.toDouble)
      )
      socket.sendText(ujson.write(payload), true)

object RobotOffloadingClient:
  import BaseWebSocketClient.*

  def fromEnvironment(robotId: ID, onOffloadChange: Boolean => Unit): RobotOffloadingClient =
    val host = env(HostEnv).getOrElse(DefaultHost)
    val port = env(PortEnv).flatMap(_.toIntOption).getOrElse(DefaultPort)
    val reconnect = env(ReconnectSecondsEnv).flatMap(_.toLongOption).map(_.seconds).getOrElse(DefaultReconnect)
    val uri = URI.create(s"ws://$host:$port/ws/robots/$robotId")
    RobotOffloadingClient(robotId, uri, reconnect, onOffloadChange)
