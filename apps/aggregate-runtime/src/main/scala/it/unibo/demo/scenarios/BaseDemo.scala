package it.unibo.demo.scenarios

import it.unibo.core.aggregate.AggregateIncarnation.{
  AggregateProgram,
  BlockC,
  BlockG,
  BlockS,
  StandardSensors
}

/** How the formation's anchor robot is chosen. */
enum AnchorMode:
  /** The operator named a leader; the shape is built around it. */
  case Leader

  /** Nobody named one, so the swarm elects it with sparse choice. */
  case Auto

/**
 * The frame a formation is laid out in.
 *
 * The origin always sits on the anchor robot, which is therefore the centre of the shape
 * and takes no slot of its own.
 *
 * @param isRoot    this device roots the collect/broadcast tree that plans the shape
 * @param potential distance field from the root, used to build that tree
 * @param toOrigin  vector from this device to the origin of the formation
 * @param reference reference heading in world axes, radians
 */
final case class AnchorFrame(
    isRoot: Boolean,
    potential: Double,
    toOrigin: (Double, Double),
    reference: Double
)

/**
 * Base class for all the demo programs.
 * 
 */
trait BaseDemo extends AggregateProgram, StandardSensors, BlockG, BlockC, BlockS:
  def distanceVector: (Double, Double) = nbrvar(NBR_VECTOR)

  def module(position: (Double, Double)): Double =
    Math.sqrt(position._1 * position._1 + position._2 * position._2)

  def normalize(position: (Double, Double)): (Double, Double) =
    val module = this.module(position)
    if module < 1e-9 then (0.0, 0.0) else (position._1 / module, position._2 / module)

  def rotate90(position: (Double, Double)): (Double, Double) =
    (-position._2, position._1)

  /** Vector from this robot to each of its neighbours, keyed by neighbour id. */
  def neighbourVectors: Map[Int, (Double, Double)] =
    foldhoodPlus[Map[Int, (Double, Double)]](Map.empty)((a, b) => a ++ b)(Map(nbr(mid()) -> distanceVector))

  /** This robot's own heading, in world axes and radians. */
  def orientation: Double = sense[Double](BaseDemo.Orientation)

  /**
   * Where the anchor comes from. Both molecules are replicated identically onto every
   * device, so branching on this splits no domain.
   */
  def anchorMode: AnchorMode = sense[String](BaseDemo.Anchor) match
    case BaseDemo.AnchorAuto => AnchorMode.Auto
    case _ =>
      // An explicit leader that is not set falls back to electing one, rather than
      // rooting every gradient on a device id that no robot has.
      if sense[Int](BaseDemo.Leader) < 0 then AnchorMode.Auto else AnchorMode.Leader

  /**
   * True on the single device the formation is rooted at: the operator's pick when there is
   * one, otherwise the winner of a sparse-choice election.
   *
   * `branch`, not `mux`: it keeps the election's `share` state from running while a manual
   * leader is set, and resets it when the operator switches, so switching gives a fresh
   * election rather than resuming a stale competition.
   */
  def isRootDevice: Boolean =
    val configured = sense[Int](BaseDemo.Leader)
    val named = configured >= 0 && mid() == configured
    // A named leader that is not actually in the fleet would leave every gradient without a
    // source, which used to freeze the whole formation without saying anything. Detecting
    // that is itself a field computation: the distance to the named device is finite
    // exactly when some device answers to that id.
    //
    // Evaluated unconditionally, and deliberately not behind `anchorMode == ... && ...`:
    // short-circuiting it would skip a `share`, so naming a leader and clearing it would
    // give the export two different shapes and misalign devices across the change.
    val namedDistance = distanceToSource(named, BaseDemo.HopMetric)
    // Unreachable for a *run* of rounds, not for a single one. The field above starts at
    // +Infinity everywhere and advances one hop per round, so on the first round after a
    // start every device reads its named leader as absent. Handing that round to the
    // election is what used to make the fleet lurch: sparse choice opens with every device
    // claiming to be a leader, so for one round every robot rooted its own formation,
    // planned a shape out of the two or three offsets its own collect had reached, and
    // broadcast it. Those plans then take several more rounds to wash out of the G/C/G
    // pipeline, and the robots spend them driving. Counting the run instead keeps the
    // operator's pick in force from the first round, and still falls back to an election
    // within `AbsenceRounds` when the named leader is genuinely missing.
    val absentFor = rep(0)(rounds => if namedDistance.isFinite then 0 else rounds + 1)
    val namedIsReachable = anchorMode == AnchorMode.Leader && absentFor <= BaseDemo.AbsenceRounds
    branch(namedIsReachable)(named)(electedRoot)

  /**
   * The sparse-choice winner, once it has held the win long enough to be worth acting on.
   *
   * The election gives up a claim one hop per round, so a device far from the eventual
   * winner keeps claiming the anchor for as many rounds as it is hops away. Acting on a
   * claim straight away means those devices root a formation they are about to lose, with
   * the same burst of wasted movement described above -- so a claim counts only once it has
   * survived `ElectionSettleRounds` in a row. The cost is that many rounds of standing
   * still at start-up, which the fleet spends filling its collect/broadcast pipeline anyway.
   */
  private def electedRoot: Boolean =
    val claimsAnchor = S(sense[Double](BaseDemo.ElectionGrain))
    rep(0)(rounds => if claimsAnchor then rounds + 1 else 0) >= BaseDemo.ElectionSettleRounds

  /**
   * Builds the frame the formation is expressed in: the origin on the root device, the
   * distance potential that roots the collection tree, and the root's heading as the shared
   * reference axis.
   */
  def anchorFrame: AnchorFrame =
    val root = isRootDevice
    val potential = gradientCast(root, 0.0, _ + nbrRange())
    val toRoot =
      gradientCast(root, (0.0, 0.0), (x, y) => (x + distanceVector._1, y + distanceVector._2))
    val reference = gradientCast(root, orientation, identity)
    AnchorFrame(root, potential, toRoot, reference)

object BaseDemo:
  /** Selected program, set from the dashboard's retained `/config/formation`. */
  val Program = "program"

  /** Operator-chosen leader id, or a negative value when the swarm should elect one. */
  val Leader = "leader"
  val NoLeader: Int = -1

  /** How the anchor is chosen: one of [[AnchorLeader]], [[AnchorAuto]]. */
  val Anchor = "anchor"
  val AnchorLeader = "leader"
  val AnchorAuto = "auto"

  /** Mean distance between two elected leaders, in hops. */
  val ElectionGrain = "electionGrain"

  /**
   * Counts hops rather than metres. Election recovery advances one `metric` per round, so
   * hops keep it at a handful of rounds instead of scaling with the link length.
   */
  val HopMetric: () => Double = () => 1.0

  /**
   * How many consecutive rounds a named leader must be unreachable before the fleet gives up
   * on it. Has to exceed the fleet's hop diameter, which is how long the distance field takes
   * to reach the far side of it after a start or a reconnection.
   */
  val AbsenceRounds: Int = 5

  /**
   * How many consecutive rounds a device must win the election before it acts as the anchor.
   * Has to exceed the hop diameter too, for the same reason: that is how long it takes the
   * eventual winner's claim to reach every device that is still claiming the anchor itself.
   */
  val ElectionSettleRounds: Int = 5

  val Orientation = "orientation"
  val CollisionArea = "collisionArea"
  val StabilityThreshold = "stabilityThreshold"

  val Defaults: Map[String, Any] = Map(
    Anchor -> AnchorLeader,
    // Well above the hop diameter of a small fleet, so sparse choice settles on exactly one
    // leader. Lowering it elects several, each growing its own regional formation.
    ElectionGrain -> 8.0
  )
