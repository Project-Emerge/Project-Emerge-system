package it.unibo.core

import cats.effect.IO
import cats.syntax.all.*
import org.typelevel.log4cats.slf4j.Slf4jLogger
import scala.concurrent.duration.*
import java.util.concurrent.atomic.{AtomicInteger, AtomicLong}

/**
 * The UpdateLoop object is responsible for the main loop of the environment update.
 * 
 */
object UpdateLoop:
  private val logger = Slf4jLogger.getLogger[IO]
  private val tickCount = new AtomicInteger(0)
  private val totalDuration = new AtomicLong(0)

  private def logDiagnostics(duration: Long, activeNodes: Int): IO[Unit] = IO {
    val count = tickCount.incrementAndGet()
    val total = totalDuration.addAndGet(duration)
    if (count >= 50) {
      if (tickCount.compareAndSet(count, 0)) {
        totalDuration.set(0)
        Some(total / 50.0)
      } else None
    } else None
  }.flatMap {
    case Some(avg) => logger.info(s"Processed 50 ticks. Active nodes count: $activeNodes. Average tick duration: $avg ms")
    case None => IO.unit
  }

  private def step[ID, Position, Info, Actuation, E <: Environment[ID, Position, Info]](
      provider: EnvironmentProvider[ID, Position, Info, E],
      coordinator: Orchestrator[ID, Position, Info, Actuation],
      actuator: EnvironmentUpdate[ID, Position, Actuation, Info, E],
      render: Boundary[ID, Position, Info]
  ): IO[Unit] =
    for
      startTime <- IO.realTimeInstant
      env       <- provider.provide()
      _         <- render.output(env).handleErrorWith(e => logger.error(e)("Error rendering environment"))
      actuations = coordinator.tick(env)
      _         <- actuations.toList.parTraverse { case (id, action) =>
                     actuator.update(env, id, action).handleErrorWith(e => logger.error(e)(s"Error updating entity $id"))
                   }
      endTime   <- IO.realTimeInstant
      duration   = endTime.toEpochMilli - startTime.toEpochMilli
      _         <- logDiagnostics(duration, env.nodes.size)
    yield ()

  /**
   * The loop function is the main loop of the environment update.
   * The loop is structured as follows:
   * 1. Get the environment from the provider
   * 2. Render the environment
   * 3. Execute the tick of the orchestrator (i.e., the AI)
   * 4. Update the environment with the actuator
   * 5. Sleep for the specified wait time
   * @param waitTimeMs the time to wait between each tick (in milliseconds)
   * @tparam ID the type of the identifier of the entities
   * @tparam Position the type of the position of the entities
   * @tparam Info the type of the information of the entities
   * @tparam Actuation the type of the actuation of the entities
   * @return an IO[Unit] that will complete when the loop is stopped
   */
  def loop[ID, Position, Info, Actuation, E <: Environment[ID, Position, Info]](waitTimeMs: Long = 1000L)(
      provider: EnvironmentProvider[ID, Position, Info, E],
      coordinator: Orchestrator[ID, Position, Info, Actuation],
      actuator: EnvironmentUpdate[ID, Position, Actuation, Info, E],
      render: Boundary[ID, Position, Info]
  ): IO[Unit] =
    val period = waitTimeMs.milliseconds
    val tick = for
      startedAt  <- IO.monotonic
      _          <- step(provider, coordinator, actuator, render)
                      .handleErrorWith(e => logger.error(e)("Error in loop step"))
      finishedAt <- IO.monotonic
      // Sleep for what is left of the period rather than a flat delay after the work: the control
      // law is tuned around a known rate, and a fixed post-work sleep quietly stretches it.
      _          <- IO.sleep((period - (finishedAt - startedAt)).max(Duration.Zero))
    yield ()
    tick.foreverM
