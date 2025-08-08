package it.unibo.core

import scala.concurrent.{ExecutionContext, Future}

/**
 * The UpdateLoop object is responsible for the main loop of the environment update.
 * 
 */
object UpdateLoop:
  private def step[ID, Position, Info, Actuation, E <: Environment[ID, Position, Info]](
      provider: EnvironmentProvider[ID, Position, Info, E],
      coordinator: Orchestrator[ID, Position, Info, Actuation],
      actuator: EnvironmentUpdate[ID, Position, Actuation, Info, E],
      render: Boundary[ID, Position, Info]
  )(using ExecutionContext) =
    for {
      int <- Future(System.currentTimeMillis().toInt)
      env <- provider.provide()
      _ <- render.output(env).recover(e => println(s"Error rendering environment: $e"))
      _ <-
        Future
          .sequence(
            coordinator
              .tick(env)
              .map((id, action) =>
                actuator.update(env, id, action).recover(e => println(s"Error updating entity $id: $e"))
              )
          )
          .recover(e => println(s"Error updating environment: $e"))
      endTime <- Future(System.currentTimeMillis().toInt)
      _ <- Future(println(s"Time: ${endTime - int}"))
    } yield ()

  /**
   * The loop function is the main loop of the environment update.
   * The loop is structured as follows:
   * 1. Get the environment from the provider
   * 2. Render the environment
   * 3. Execute the tick of the orchestrator (i.e., the AI)
   * 4. Update the environment with the actuator
   * 5. Sleep for the specified wait time
   * @param waitTime the time to wait between each tick (in milliseconds)
   * @tparam ID the type of the identifier of the entities
   * @tparam Position the type of the position of the entities
   * @tparam Info the type of the information of the entities
   * @tparam Actuation the type of the actuation of the entities
   * @return a Future[Unit] that will complete when the loop is stopped
   */
  def loop[ID, Position, Info, Actuation, E <: Environment[ID, Position, Info]](waitTime: Long = 1000L)(
      provider: EnvironmentProvider[ID, Position, Info, E],
      coordinator: Orchestrator[ID, Position, Info, Actuation],
      actuator: EnvironmentUpdate[ID, Position, Actuation, Info, E],
      render: Boundary[ID, Position, Info]
  )(using ExecutionContext): Future[Unit] =
    step(provider, coordinator, actuator, render)
      .recover(e => println(s"Error in loop: ${e.printStackTrace()}"))
      .flatMap(_ => Future(Thread.sleep(waitTime)))
      .flatMap(_ => loop(waitTime)(provider, coordinator, actuator, render))
