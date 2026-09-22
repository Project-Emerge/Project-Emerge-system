package it.unibo.core.aggregate

import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.{DistanceEstimator, Environment, Orchestrator}
import org.slf4j.LoggerFactory
import scala.util.control.NonFatal

/** Central orchestrator for aggregate computing rounds.
  *
  * @param haltOnFailure command returned for an agent whose round fails.
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

  /** Retain silent devices' exports briefly to preserve `rep`/`share` state. */
  private val exportRetention: Long = 5_000L
  private var lastSeen: Map[Int, Long] = Map.empty
  private val failureLogInterval: Long = 5_000L
  private var lastFailureLogMillis: Long = 0L

  override def tick(world: Environment[Int, Position, Map[String, Any]]): Map[Int, Actuation] =
    // Read once per round to keep time-dependent devices in phase.
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
              // Isolate failures so the rest of the fleet still receives commands.
          reportRoundFailure(currentAgent, tickMillis, error)
              // Drop the failed export to reset its `rep`/`share` state next round.
          haltOnFailure.foreach(halt => actuations += (currentAgent -> halt))
    }
    // Retained exports are only used to resume a device's state when it returns.
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