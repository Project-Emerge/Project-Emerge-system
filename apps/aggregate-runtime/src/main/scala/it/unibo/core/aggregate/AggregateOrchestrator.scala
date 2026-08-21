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
    val nodes = world.nodes
    val exportsBuilder = Map.newBuilder[Int, EXPORT]
    nodes.foreach { currentAgent =>
      val ctx = contextFromAgent(currentAgent, world)
      val agentExport = adaptExport(program.round(ctx))
      exportsBuilder += (currentAgent -> agentExport)
    }
    exports = exportsBuilder.result()
    exports.map((agent, ex) => agent -> ex.root[Actuation]())

  private def contextFromAgent(agent: Int, world: Environment[Int, Position, Map[String, Any]]): CONTEXT =
    val myPosition = world.position(agent)
    val myInfo = world.sensing(agent)
    val estimator = summon[DistanceEstimator[Position]]

    val neighboursPositionBuilder = Map.newBuilder[Int, Position]
    val neighboursExportsBuilder = Map.newBuilder[Int, EXPORT]
    val neighboursDistancesBuilder = Map.newBuilder[Int, Double]
    val neighboursDistancesVectorBuilder = Map.newBuilder[Int, Position]

    val activeNodes = world.nodes

    def processNode(n: Int): Unit =
      if activeNodes.contains(n) then
        val pos = world.position(n)
        neighboursPositionBuilder += (n -> pos)
        val exp = exports.getOrElse(n, factory.emptyExport())
        neighboursExportsBuilder += (n -> exp)
        neighboursDistancesBuilder += (n -> estimator.distance(myPosition, pos))
        neighboursDistancesVectorBuilder += (n -> estimator.distanceVector(myPosition, pos))

    val ns = world.neighbors(agent)
    ns.foreach(processNode)
    processNode(agent)

    val localSensors = myInfo + (
      LSNS_POSITION -> myPosition
    )

    factory.context(
      selfId = agent,
      exports = neighboursExportsBuilder.result(),
      lsens = localSensors,
      nbsens = Map(
        NBR_RANGE -> neighboursDistancesBuilder.result(),
        NBR_VECTOR -> neighboursDistancesVectorBuilder.result()
      )
    )

  private def adaptExport(exp: EXPORT): EXPORT =
    if(exp.root().getClass.isAssignableFrom(classOf[ExportImpl])) then
      exp.root()
    else exp