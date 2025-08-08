package it.unibo.core.aggregate

import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.{DistanceEstimator, Environment, Orchestrator}

/**
 * An aggregate computing central orchestrator that receives the state of the world and returns the actuation for each agent.
 * @param agents
 * @tparam Position
 * @tparam Info
 * @tparam Actuation
 */
class AggregateOrchestrator[Position, Info, Actuation](
    agents: Set[Int],
    program: AggregateProgram
)(using DistanceEstimator[Position])
    extends Orchestrator[Int, Position, Info, Actuation]:
  private val sensorsNames = new StandardSensorNames {}
  import sensorsNames.*
  var exports: Map[Int, EXPORT] =
    agents.map(_ -> factory.emptyExport()).toMap
  override def tick(world: Environment[Int, Position, Info]): Map[Int, Actuation] =
    exports = (for
      currentAgent <- agents.intersect(world.nodes)
      ctx = contextFromAgent(currentAgent, world)
      agentExport = program.round(ctx)
    yield currentAgent -> agentExport).toMap
    exports.map((agent, ex) => agent -> ex.root[Actuation]())

  private def contextFromAgent(agent: Int, world: Environment[Int, Position, Info]): CONTEXT =
    val neighbours = world.neighbors(agent) + agent
    val neighboursPosition = neighbours.map(n => n -> world.position(n)).toMap
    val myPosition = world.position(agent)
    val myInfo = world.sensing(agent)
    val localSensors = Map(
      "info" -> myInfo,
      LSNS_POSITION -> myPosition
    )
    val neighboursExports = neighbours
      .map(n => n -> exports.getOrElse(n, factory.emptyExport()))
      .toMap + (agent -> exports.getOrElse(agent, factory.emptyExport()))
    val neighboursDistances =
      neighboursPosition.map((n, p) => n -> summon[DistanceEstimator[Position]].distance(myPosition, p))
    val neighboursDistancesVector =
      neighboursPosition
        .map((n, p) => n -> summon[DistanceEstimator[Position]].distanceVector(myPosition, p))
    factory.context(
      selfId = agent,
      exports = neighboursExports,
      lsens = localSensors,
      nbsens = Map(NBR_RANGE -> neighboursDistances, NBR_VECTOR -> neighboursDistancesVector)
    )
