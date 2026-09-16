package it.unibo.demo

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.robot.Actuation
import it.unibo.demo.scenarios.*
import it.unibo.utils.Position.given

/**
 * What a fleet does when the tracker keeps losing sight of a robot.
 *
 * A robot takes part in a round only while it has a pose, so a tracker that drops one every
 * few frames changes how many robots the shape is being laid out for, over and over. That is
 * not a small perturbation: a ring of five slots puts every one of them at a different
 * bearing from a ring of six, so a single missed frame reassigns the whole fleet. Left
 * uncorrected the fleet holds roughly the right shape and twitches around it forever, which
 * on the floor reads as a formation that never quite works.
 *
 * Exercised through [[AllDemoToLoad]] rather than a formation class, since that is what the
 * fleet actually runs.
 */
class TrackerGapSuite extends munit.FunSuite:

  /** @param unseen robots the tracker has lost this round; they get no pose and no round. */
  private final class World(
      val positions: Map[Int, (Double, Double)],
      val config: Map[String, Any],
      val unseen: Set[Int]
  ) extends Environment[Int, (Double, Double), Map[String, Any]]:
    override def nodes: Set[Int] = positions.keySet -- unseen
    override def position(id: Int): (Double, Double) = positions(id)
    override def sensing(id: Int): Map[String, Any] = config + (BaseDemo.Orientation -> 0.0)
    override def neighbors(id: Int): Set[Int] = nodes

  private val radius = 0.6

  private val config: Map[String, Any] = FormationDefaults.All ++ Map(
    BaseDemo.Program -> "circleShape",
    BaseDemo.Leader -> 0,
    BaseDemo.Anchor -> BaseDemo.AnchorLeader,
    BaseDemo.CollisionArea -> 0.3,
    BaseDemo.StabilityThreshold -> 0.1,
    CircleFormation.RADIUS_SENSING -> radius
  )

  private val seven: Map[Int, (Double, Double)] =
    (0 until 7).map(i => i -> ((i % 4) * 0.5 + 0.2, (i / 4) * 0.5 + 0.2)).toMap

  /**
   * Runs the fleet, hiding `lost` from the tracker on every `everyNthRound`-th round, and
   * reports where it ended up along with how far it was still travelling over the last
   * `tail` rounds. A settled fleet travels none.
   */
  private def run(
      lost: Set[Int],
      everyNthRound: Int,
      rounds: Int = 800,
      tail: Int = 200,
      step: Double = 0.02
  ): (Map[Int, (Double, Double)], Double) =
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](AllDemoToLoad(FormationDefaults.Programs*))
    var positions = seven
    var residual = 0.0
    (1 to rounds).foreach { round =>
      val unseen = if everyNthRound > 0 && round % everyNthRound == 0 then lost else Set.empty[Int]
      val actuations = orchestrator.tick(World(positions, config, unseen))
      positions = positions.map { (id, at) =>
        actuations.get(id) match
          case Some(Actuation.Forward(direction, distance)) =>
            val travelled = math.min(distance, step)
            if round > rounds - tail then residual += travelled
            id -> (at._1 + direction._1 * travelled, at._2 + direction._2 * travelled)
          case _ => id -> at
      }
    }
    (positions, residual)

  private def radiiAround(positions: Map[Int, (Double, Double)], anchor: Int, ignoring: Set[Int]): List[Double] =
    val origin = positions(anchor)
    (positions -- ignoring - anchor).values
      .map(p => math.hypot(p._1 - origin._1, p._2 - origin._2))
      .toList

  test("a robot the tracker keeps dropping does not stop the fleet settling") {
    List(7, 3, 2).foreach { everyNthRound =>
      val (settled, residual) = run(Set(6), everyNthRound)
      assertEquals(
        residual,
        0.0,
        s"with a robot lost every $everyNthRound rounds the fleet was still moving: $residual m"
      )
      radiiAround(settled, 0, Set.empty).foreach { r =>
        assert(math.abs(r - radius) < 0.12, s"a robot settled ${r}m from the anchor, expected about $radius m")
      }
    }
  }

  test("a robot that has really gone lets the shape close up without it") {
    // The slot is held only for a while: a fleet permanently down one robot must re-lay the
    // ring for the robots it still has, rather than leaving a gap where the lost one was.
    val orchestrator = AggregateOrchestrator[(Double, Double), Actuation](AllDemoToLoad(FormationDefaults.Programs*))
    var positions = seven
    def advance(rounds: Int, unseen: Set[Int]): Unit =
      (1 to rounds).foreach { _ =>
        val actuations = orchestrator.tick(World(positions, config, unseen))
        positions = positions.map { (id, at) =>
          actuations.get(id) match
            case Some(Actuation.Forward(direction, distance)) =>
              val travelled = math.min(distance, 0.02)
              id -> (at._1 + direction._1 * travelled, at._2 + direction._2 * travelled)
            case _ => id -> at
        }
      }
    advance(400, Set.empty)
    advance(600, Set(6))

    val origin = positions(0)
    radiiAround(positions, 0, Set(6)).foreach { r =>
      assert(math.abs(r - radius) < 0.12, s"a robot sits ${r}m from the anchor, expected about $radius m")
    }
    // Five robots on the ring means gaps of about 72 degrees; a held-open slot would leave
    // one gap of about twice that.
    val bearings = (positions - 0 - 6).values
      .map(p => math.atan2(p._1 - origin._1, p._2 - origin._2))
      .toList
      .sorted
    val gaps = bearings.zip(bearings.tail :+ (bearings.head + 2 * math.Pi)).map((a, b) => math.toDegrees(b - a))
    gaps.foreach(gap => assert(math.abs(gap - 72.0) < 15.0, s"the ring did not close up evenly: $gaps"))
  }
