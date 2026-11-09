package it.unibo.core

/**
 * Orchestrator is the main component of the system. It is responsible for orchestrating the system.
 * It receives the current state of the world and returns the actuation to be performed.
 * The actuation is a map from the ID of the entity to the actuation to be performed.
 * @tparam ID The type of the entity ID
 * @tparam Position The type of the entity position
 * @tparam Info The type of the entity info
 * @tparam Actuation The type of the actuation
 */
trait Orchestrator[ID, Position, Info, Actuation]:
  /**
     * Given the current state of the world, returns the actuation to be performed.
     * @param world The current state of the world
     * @return The actuation to be performed
     */
  def tick(world: Environment[ID, Position, Info]): Map[ID, Actuation]
