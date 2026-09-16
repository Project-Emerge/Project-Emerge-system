package it.unibo.core.aggregate

import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.{DistanceEstimator, Environment, Orchestrator}
import org.slf4j.LoggerFactory
import scala.util.control.NonFatal

/**
 * An aggregate computing central orchestrator that receives the state of the world and returns the actuation for each agent.
 * @param agents
 * @tparam Position
 * @tparam Info
 * @tparam Actuation
 */
/**
 * @param haltOnFailure what to command a robot whose own round threw. `Some` is strongly
 *                      preferred for a physical fleet: leaving a robot out of the result
 *                      means no command is published and it keeps driving on the last one.
 */
class AggregateOrchestrator[Position, Actuation](
    program: AggregateProgram,
    haltOnFailure: Option[Actuation] = None
)(using DistanceEstimator[Position])
    extends Orchestrator[Int, Position, Map[String, Any], Actuation]:
  private val logger = LoggerFactory.getLogger(classOf[AggregateOrchestrator[?, ?]])
  private val sensorsNames = new StandardSensorNames {}
  import sensorsNames.*
  var exports: Map[Int, EXPORT] = Map.empty

  /**
   * How long the export of a silent device is kept around. A robot only appears in
   * `world.nodes` if it published a pose during the last tick, so without this a single
   * missed publication would wipe all of its `rep`/`share` state and restart every
   * gradient, election and collection it takes part in.
   */
  private val exportRetention: Long = 5_000L
  private var lastSeen: Map[Int, Long] = Map.empty
  private val failureLogInterval: Long = 5_000L
  private var lastFailureLogMillis: Long = 0L

  override def tick(world: Environment[Int, Position, Map[String, Any]]): Map[Int, Actuation] =
    // Read the clock once for the whole round. Reading it per device would let devices
    // computed early and late in this loop land on opposite sides of a phase boundary,
    // which would misalign the ones that branch on a time-derived value.
    val tickMillis: Long = System.currentTimeMillis()
    val nodes = world.nodes
    val roundExports = Map.newBuilder[Int, EXPORT]
    val actuations = Map.newBuilder[Int, Actuation]
    nodes.foreach { currentAgent =>
      try
        val ctx = contextFromAgent(currentAgent, world, tickMillis)
        val agentExport = adaptExport(program.round(ctx))
        roundExports += (currentAgent -> agentExport)
        actuations += (currentAgent -> agentExport.root[Actuation]())
      catch
        case NonFatal(error) =>
          // One robot's round must not take the whole fleet down with it. Without this a
          // single failure aborts the tick, no robot is sent a command at all, and every
          // robot carries on driving with the last one it received.
          reportRoundFailure(currentAgent, tickMillis, error)
          // Drop this robot's export rather than keeping it. A stale export is usually
          // what caused the failure in the first place, so retaining it would make the
          // fault self-perpetuating for that robot; dropping it restarts its `rep`/`share`
          // state next round, and its neighbours simply fall back to their fold defaults
          // for one round.
          haltOnFailure.foreach(halt => actuations += (currentAgent -> halt))
    }
    // Retaining an absent device's export is safe: `contextFromAgent` only reads the
    // exports of currently active nodes, so a retained entry is never used as neighbour
    // data -- it only lets the device resume its own state if it comes back.
    lastSeen = (lastSeen ++ nodes.map(_ -> tickMillis)).filter { (_, seen) =>
      tickMillis - seen <= exportRetention
    }
    exports = (exports ++ roundExports.result()).filter((id, _) => lastSeen.contains(id))
    actuations.result()

  private def contextFromAgent(
      agent: Int,
      world: Environment[Int, Position, Map[String, Any]],
      tickMillis: Long
  ): CONTEXT =
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

    val localSensors = myInfo
      + (LSNS_POSITION -> myPosition)
      // Shared across every device in this round, so time-varying formations stay in phase.
      + (LSNS_TIMESTAMP -> tickMillis)

    factory.context(
      selfId = agent,
      exports = neighboursExportsBuilder.result(),
      lsens = localSensors,
      nbsens = Map(
        NBR_RANGE -> neighboursDistancesBuilder.result(),
        NBR_VECTOR -> neighboursDistancesVectorBuilder.result()
      )
    )

  /** Throttled, so a persistent fault cannot flood the log at the tick rate. */
  private def reportRoundFailure(agent: Int, tickMillis: Long, error: Throwable): Unit =
    if tickMillis - lastFailureLogMillis >= failureLogInterval then
      lastFailureLogMillis = tickMillis
      logger.warn(s"Aggregate round failed for agent $agent; halting it for this tick", error)

  private def adaptExport(exp: EXPORT): EXPORT =
    if(exp.root().getClass.isAssignableFrom(classOf[ExportImpl])) then
      exp.root()
    else exp