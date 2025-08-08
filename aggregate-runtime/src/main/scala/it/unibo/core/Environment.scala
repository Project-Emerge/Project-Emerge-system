package it.unibo.core

/**
 * An environment is a collection of nodes, each of which has a position and some information (context dependent).
 * This is a snapshot of the environment at a given time.
 * @tparam ID The type of the node identifier
 * @tparam Position The type of the node position
 * @tparam Info The type of the node information (e.g., Double for a temperature, String for a label, etc.)
 */
trait Environment[ID, Position, Info]:
  def nodes: Set[ID]
  def position(id: ID): Position
  def sensing(id: ID): Info
  def neighbors(id: ID): Set[ID]
