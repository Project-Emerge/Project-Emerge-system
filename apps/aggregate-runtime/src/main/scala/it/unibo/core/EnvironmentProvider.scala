package it.unibo.core

import scala.concurrent.Future

/**
 * EnvironmentProvider is a trait that provides an Environment.
 * This, in particular, compute a current snapshot of the environment, which can be used by the orchestrator to
 * take decisions. 
 * @tparam ID the type of the identifier of the nodes
 * @tparam Position the type of the position of the nodes
 * @tparam Info the type of the information of the nodes
 */
trait EnvironmentProvider[ID, Position, Info, +E <: Environment[ID, Position, Info]]:
  /**
   * This method provides an Environment.
   * It returns a Future since the computation of the Environment could be time-consuming and it could be
   * performed asynchronously.
   * @return a Future that will be completed with the Environment
   */
  def provide(): Future[E]
