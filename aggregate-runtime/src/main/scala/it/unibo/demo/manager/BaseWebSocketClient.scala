package it.unibo.demo.manager

import org.slf4j.LoggerFactory

import java.net.URI
import java.net.http.WebSocket.Listener
import java.net.http.{HttpClient, WebSocket}
import java.util.concurrent.atomic.{AtomicBoolean, AtomicReference}
import java.util.concurrent.{CompletionStage, Executors, TimeUnit}
import scala.concurrent.duration.*

/** Abstract base class for WebSocket clients interacting with the Offloading Manager.
  *
  * It abstracts connection establishment, automatic reconnection, thread-safe buffering of
  * multi-part incoming text messages, and basic environment configuration parsing.
  */
abstract class BaseWebSocketClient(
    val uri: URI,
    val reconnectDelay: FiniteDuration,
    threadName: String
):
  protected val logger = LoggerFactory.getLogger(getClass)
  private val httpClient = HttpClient.newHttpClient()
  private val scheduler = Executors.newSingleThreadScheduledExecutor: runnable =>
    val thread = new Thread(runnable, threadName)
    thread.setDaemon(true)
    thread
  private val running = new AtomicBoolean(false)
  private val reconnectPending = new AtomicBoolean(false)
  protected val socketRef = new AtomicReference[WebSocket]()

  protected def logPrefix: String = ""

  def start(): Unit =
    if running.compareAndSet(false, true) then connect()

  def stop(): Unit =
    running.set(false)
    scheduler.shutdownNow()
    onStop()
    Option(socketRef.getAndSet(null)).foreach(_.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown"))

  protected def onStop(): Unit = ()

  protected def onConnect(webSocket: WebSocket): Unit = ()

  protected def handleMessage(message: String): Unit

  private def connect(): Unit =
    if !running.get() then return
    logger.info(s"${logPrefix}connecting to offloading manager at $uri")
    httpClient
      .newWebSocketBuilder()
      .buildAsync(uri, new ClientListener)
      .whenComplete: (socket, error) =>
        if error != null then
          logger.warn(s"${logPrefix}cannot reach offloading manager at $uri, retrying in ${reconnectDelay.toSeconds}s")
          logger.debug(s"${logPrefix}connection failure", error)
          scheduleReconnect()
        else
          socketRef.set(socket)
          reconnectPending.set(false)
          logger.info(s"${logPrefix}connected to offloading manager")
          onConnect(socket)

  private def scheduleReconnect(): Unit =
    if running.get() && reconnectPending.compareAndSet(false, true) then
      scheduler.schedule(
        (() =>
          reconnectPending.set(false)
          connect()): Runnable,
        reconnectDelay.toMillis,
        TimeUnit.MILLISECONDS
      )

  private final class ClientListener extends Listener:
    private val buffer = new StringBuilder

    override def onOpen(webSocket: WebSocket): Unit =
      webSocket.request(1)

    override def onText(webSocket: WebSocket, data: CharSequence, last: Boolean): CompletionStage[?] =
      buffer.append(data)
      if last then
        val message = buffer.toString
        buffer.setLength(0)
        handleMessage(message)
      webSocket.request(1)
      null

    override def onClose(webSocket: WebSocket, statusCode: Int, reason: String): CompletionStage[?] =
      logger.info(s"${logPrefix}disconnected from offloading manager ($statusCode)${if reason.nonEmpty then s": $reason" else ""}")
      socketRef.set(null)
      scheduleReconnect()
      null

    override def onError(webSocket: WebSocket, error: Throwable): Unit =
      logger.warn(s"${logPrefix}WebSocket error", error)
      socketRef.set(null)
      scheduleReconnect()

object BaseWebSocketClient:
  val HostEnv = "OFFLOADING_MANAGER_HOST"
  val PortEnv = "OFFLOADING_MANAGER_PORT"
  val ReconnectSecondsEnv = "OFFLOADING_MANAGER_RECONNECT_SECONDS"
  val DefaultHost = "offloading-manager"
  val DefaultPort = 8000
  val DefaultReconnect = 5.seconds

  def env(name: String): Option[String] =
    Option(System.getenv(name)).map(_.trim).filter(_.nonEmpty)
