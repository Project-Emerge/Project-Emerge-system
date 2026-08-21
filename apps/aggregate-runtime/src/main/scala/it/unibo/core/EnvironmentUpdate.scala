package it.unibo.core

import scala.concurrent.Future

/**
 * An environment update is a function that takes a node and an actuation and try to update the environment accordingly.
 * @tparam ID The type of the node identifier
 * @tparam Actuation The type of the actuation
 */
trait EnvironmentUpdate[ID, Position, Actuation, Info, -E <: Environment[ID, Position, Info]]:
  /**
   * Update the environment with the given actuation.
   * It returns a future that will complete when the update is done.
   * It can fail if the actuation is not valid for the given node.
   * @param node The node to update
   * @param actuation The actuation to apply
   * @return A future that will complete when the update is done
   */
  def update(world: E, id: ID, actuation: Actuation): Future[Unit]
