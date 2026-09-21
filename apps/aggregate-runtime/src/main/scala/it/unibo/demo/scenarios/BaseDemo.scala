package it.unibo.demo.scenarios

import it.unibo.core.aggregate.AggregateIncarnation.{
  AggregateProgram,
  BlockC,
  BlockG,
  BlockS,
  StandardSensors
}

enum AnchorMode:
  case Leader
  case Auto

/** Origin sits on the anchor, so the anchor takes no slot. `reference` is world axes, radians. */
final case class AnchorFrame(
    isRoot: Boolean,
    potential: Double,
    toOrigin: (Double, Double),
    reference: Double
)

trait BaseDemo extends AggregateProgram, StandardSensors, BlockG, BlockC, BlockS:
  def distanceVector: (Double, Double) = nbrvar(NBR_VECTOR)

  def module(position: (Double, Double)): Double =
    Math.sqrt(position._1 * position._1 + position._2 * position._2)

  def normalize(position: (Double, Double)): (Double, Double) =
    val module = this.module(position)
    if module < 1e-9 then (0.0, 0.0) else (position._1 / module, position._2 / module)

  def rotate90(position: (Double, Double)): (Double, Double) =
    (-position._2, position._1)

  def neighbourVectors: Map[Int, (Double, Double)] =
    foldhoodPlus[Map[Int, (Double, Double)]](Map.empty)((a, b) => a ++ b)(Map(nbr(mid()) -> distanceVector))

  def orientation: Double = sense[Double](BaseDemo.Orientation)

  // Both molecules are replicated identically everywhere, so branching here splits no domain.
  def anchorMode: AnchorMode = sense[String](BaseDemo.Anchor) match
    case BaseDemo.AnchorAuto => AnchorMode.Auto
    case _ => if sense[Int](BaseDemo.Leader) < 0 then AnchorMode.Auto else AnchorMode.Leader

  /** `branch`, not `mux`: a switch resets the election's `share` instead of resuming a stale one. */
  def isRootDevice: Boolean =
    val configured = sense[Int](BaseDemo.Leader)
    val named = configured >= 0 && mid() == configured
    // Always evaluate this field to preserve the export shape when the leader changes.
    val namedDistance = distanceToSource(named, BaseDemo.HopMetric)
    val absentFor = rep(0)(rounds => if namedDistance.isFinite then 0 else rounds + 1)
    val namedIsReachable = anchorMode == AnchorMode.Leader && absentFor <= BaseDemo.AbsenceRounds
    branch(namedIsReachable)(named)(electedRoot)

  /** Claims retract one hop per round, so acting on a fresh one roots a formation about to be lost. */
  private def electedRoot: Boolean =
    val claimsAnchor = S(sense[Double](BaseDemo.ElectionGrain))
    rep(0)(rounds => if claimsAnchor then rounds + 1 else 0) >= BaseDemo.ElectionSettleRounds

  def anchorFrame: AnchorFrame =
    val root = isRootDevice
    val potential = gradientCast(root, 0.0, _ + nbrRange())
    val toRoot =
      gradientCast(root, (0.0, 0.0), (x, y) => (x + distanceVector._1, y + distanceVector._2))
    val reference = gradientCast(root, orientation, identity)
    AnchorFrame(root, potential, toRoot, reference)

object BaseDemo:
  /** Set from the dashboard's retained `/config/formation`. */
  val Program = "program"

  val Leader = "leader"
  val NoLeader: Int = -1

  val Anchor = "anchor"
  val AnchorLeader = "leader"
  val AnchorAuto = "auto"

  /** Mean hops between two elected leaders. */
  val ElectionGrain = "electionGrain"

  /** Hops, not metres: recovery advances one metric per round. */
  val HopMetric: () => Double = () => 1.0

  /** Rounds of absence before giving up on a named leader. Must exceed the hop diameter. */
  val AbsenceRounds: Int = 5

  /** Rounds of winning before acting as anchor. Must exceed the hop diameter too. */
  val ElectionSettleRounds: Int = 5

  val Orientation = "orientation"
  val CollisionArea = "collisionArea"
  val StabilityThreshold = "stabilityThreshold"

  val Defaults: Map[String, Any] = Map(
    Anchor -> AnchorLeader,
    // Above the hop diameter, so exactly one leader wins; lower elects several formations.
    ElectionGrain -> 8.0
  )
