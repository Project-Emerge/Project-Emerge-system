package it.unibo.core.aggregate

import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.{DistanceEstimator, Environment, Orchestrator}

/** A *distributed* aggregate orchestrator: unlike [[AggregateOrchestrator]] (which computes the whole
  * world in one VM), this one computes only the nodes it OWNS, and gets every other node's export from
  * the outside — i.e. exchanged over MQTT.
  *
  * Each round it:
  *   1. computes a round only for the owned nodes, using, for each neighbour, the freshest export
  *      available (its own last round if it owns that neighbour, otherwise the one received remotely);
  *   2. publishes each owned node's fresh export (so other runtimes can perceive these nodes);
  *   3. returns the actuation only for the owned nodes.
  *
  * This makes the computation truly distributed: the central runtime and an edge runtime can each own a
  * disjoint subset of robots and still compute one coherent field, as long as they exchange exports.
  *
  * @param ownedNodes        nodes this runtime is responsible for (re-evaluated every round)
  * @param publishExport     sink for an owned node's fresh export (e.g. publish to MQTT)
  * @param receivedExport    source of a non-owned node's latest export (e.g. received from MQTT)
  */
class DistributedAggregateOrchestrator[Position, Actuation](
    program: AggregateProgram,
    ownedNodes: Environment[Int, Position, Map[String, Any]] => Set[Int],
    publishExport: (Int, EXPORT) => Unit,
    receivedExport: Int => Option[EXPORT]
)(using DistanceEstimator[Position])
    extends Orchestrator[Int, Position, Map[String, Any], Actuation]:
  private val sensorsNames = new StandardSensorNames {}
  import sensorsNames.*

  /** Exports of the nodes WE computed in the previous round. */
  private var localExports: Map[Int, EXPORT] = Map.empty

  /** Snapshot of the exports this runtime computed last round (for monitoring/telemetry). */
  def lastExports: Map[Int, EXPORT] = localExports

  /** Freshest export for a node: our own if we computed it, otherwise the remotely received one. */
  private def exportOf(id: Int): EXPORT =
    localExports.get(id).orElse(receivedExport(id)).getOrElse(factory.emptyExport())

  override def tick(world: Environment[Int, Position, Map[String, Any]]): Map[Int, Actuation] =
    val owned = ownedNodes(world).intersect(world.nodes)
    val newLocal = (for
      agent <- owned
      ctx = contextFromAgent(agent, world)
      agentExport = adaptExport(program.round(ctx))
    yield agent -> agentExport).toMap
    newLocal.foreach((id, ex) => publishExport(id, ex))
    localExports = newLocal
    newLocal.map((agent, ex) => agent -> ex.root[Actuation]())

  private def contextFromAgent(agent: Int, world: Environment[Int, Position, Map[String, Any]]): CONTEXT =
    val neighbours = (world.neighbors(agent) + agent).intersect(world.nodes)
    val neighboursPosition = neighbours.map(n => n -> world.position(n)).toMap
    val myPosition = world.position(agent)
    val myInfo = world.sensing(agent)

    val localSensors = myInfo + (LSNS_POSITION -> myPosition)
    val neighboursExports = neighbours.map(n => n -> exportOf(n)).toMap + (agent -> exportOf(agent))
    val neighboursDistances =
      neighboursPosition.map((n, p) => n -> summon[DistanceEstimator[Position]].distance(myPosition, p))
    val neighboursDistancesVector =
      neighboursPosition.map((n, p) => n -> summon[DistanceEstimator[Position]].distanceVector(myPosition, p))

    factory.context(
      selfId = agent,
      exports = neighboursExports,
      lsens = localSensors,
      nbsens = Map(NBR_RANGE -> neighboursDistances, NBR_VECTOR -> neighboursDistancesVector)
    )

  private def adaptExport(exp: EXPORT): EXPORT =
    if exp.root().getClass.isAssignableFrom(classOf[ExportImpl]) then exp.root()
    else exp
