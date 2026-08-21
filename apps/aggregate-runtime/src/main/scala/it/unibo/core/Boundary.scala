package it.unibo.core

import scala.concurrent.Future

/**
 * A boundary is a component that interacts with the external world.
 * It is used to output the state of the environment -- e.g., to a file, a database, or in a graphical user interface.
 * @tparam ID The type of the identifier of the entities in the environment
 * @tparam Position The type of the position of the entities in the environment
 * @tparam Info The type of the information associated with the entities in the environment
 */
trait Boundary[ID, Position, Info]:
  def output(environment: Environment[ID, Position, Info]): Future[Unit]
