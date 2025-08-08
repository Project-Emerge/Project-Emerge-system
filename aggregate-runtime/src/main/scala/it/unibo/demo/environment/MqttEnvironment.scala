package it.unibo.demo.environment

import it.unibo.core.Environment
import it.unibo.demo.{ID, Info}
import it.unibo.utils.Position.{Position, given}

/**
 * A simple snapshot of the environment created from a web service.
 *
 * @param data A map of node IDs to their positions and information.
 * @param neighboursRadius The radius within which nodes are considered neighbors.
 */
class MqttEnvironment(data: Map[ID, (Position, Info)], private val neighbours: Map[ID, Set[ID]])
    extends Environment[ID, Position, Info]:

  override def nodes: Set[ID] = data.keySet

  override def position(id: ID): (Double, Double) = data(id)._1

  override def sensing(id: ID): Info = data(id)._2

  override def neighbors(id: ID): Set[ID] =
    neighbours.getOrElse(id, Set.empty)
