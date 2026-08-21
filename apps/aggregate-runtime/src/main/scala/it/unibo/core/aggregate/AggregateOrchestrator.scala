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
class AggregateOrchestrator[Position, Actuation](
    program: AggregateProgram
)(using DistanceEstimator[Position])
    extends Orchestrator[Int, Position, Map[String, Any], Actuation]:
  private val sensorsNames = new StandardSensorNames {}
  import sensorsNames.*
  var exports: Map[Int, EXPORT] = Map.empty
  override def tick(world: Environment[Int, Position, Map[String, Any]]): Map[Int, Actuation] =
    exports = (for
      currentAgent <- world.nodes
      ctx = contextFromAgent(currentAgent, world)
      agentExport = adaptExport(program.round(ctx))
    yield currentAgent -> agentExport).toMap
    exports.map((agent, ex) => agent -> ex.root[Actuation]())

  private def contextFromAgent(agent: Int, world: Environment[Int, Position, Map[String, Any]]): CONTEXT =
    val neighbours = world.neighbors(agent) + agent
    val neighboursPosition = neighbours.intersect(world.nodes)
      .map(n => n -> world.position(n)).toMap
    val myPosition = world.position(agent)
    val myInfo = world.sensing(agent)

    val localSensors = myInfo + (
      LSNS_POSITION -> myPosition
    )
    val neighboursExports = neighbours.intersect(world.nodes)
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

  private def adaptExport(exp: EXPORT): EXPORT =
    if(exp.root().getClass.isAssignableFrom(classOf[ExportImpl])) then
      exp.root()
    else exp