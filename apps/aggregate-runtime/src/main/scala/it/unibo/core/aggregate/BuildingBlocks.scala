package it.unibo.core.aggregate

import it.unibo.scafi.incarnations.Incarnation

/**
 * Building blocks for aggregate programs.
 *  They are based on the `Incarnation` trait, which is a type family that defines the types used in the program.
 *  
 */
trait BuildingBlocks:
  self: Incarnation =>

  type ID = Int

  /**
   * How a gradient measures the step from a device to one of its neighbours.
   * `() => nbrRange()` measures metres, `() => 1.0` measures hops.
   */
  type Metric = () => Double

  trait BlockG:
    self: AggregateProgram & StandardSensors =>
    def gradientCast[A](source: Boolean, center: A, accumulation: A => A): A =
      rep((Double.PositiveInfinity, center)) { accumulateData =>
        {
          mux(source)((0.0, center)) {
            foldhoodPlus((Double.PositiveInfinity, center))(minByFirst)(
              accumulateAndCast[A](nbr(accumulateData), accumulation)
            )
          }
        }
      }._2

    private def accumulateAndCast[A](data: (Double, A), accumulation: A => A): (Double, A) =
      (data._1 + nbrRange(), accumulation(data._2))

    /** Distance to the closest source, or `+Infinity` when none is reachable. */
    def distanceToSource(source: Boolean, metric: Metric): Double =
      share(Double.PositiveInfinity) { (_, neighbourDistance) =>
        mux(source)(0.0)(minHoodPlus(neighbourDistance() + metric()))
      }

  trait BlockT:
    self: AggregateProgram =>
    def decay[T](initial: T, floor: T, decayWith: T => T): T =
      rep(initial)(value => mux(value == floor)(floor)(decayWith(value)))

  trait BlockC:
    self: AggregateProgram & StandardSensors =>

    def collectCast[V](potential: Double, accumulation: (V, V) => V, local: V, Null: V): V =
      rep(local): collected =>
        accumulation(
          local,
          foldhood(Null)(accumulation) {
            mux(nbr(findParent(potential)) == mid())(nbr(collected))(nbr(Null))
          }
        )

    def findParent(potential: Double): ID =
      val (minPotential, minId) = foldhood((Double.MaxValue, mid()))(minByFirst)(nbr((potential, mid())))
      if (minPotential < potential) minId else Builtins.Bounded.of_i.top

  /**
   * Sparse choice: the swarm elects its own leaders, roughly `grain` apart, with no
   * external input. 
   */
  trait BlockS:
    self: AggregateProgram & StandardSensors & BlockG =>

    /** A totally ordered device identity. `Bounded` compares the first element, then the id. */
    type Uid = (Double, ID)

    private given uidBounded: Builtins.Bounded[Uid] =
      Builtins.Bounded.tupleBounded[Double, ID]

    /** @param grain mean leader distance in `metric` units; above network diameter, one leader.
      * @param metric distance metric; defaults to hops for `grain`-round recovery.
      */
    def S(grain: Double, metric: Metric = () => 1.0): Boolean =
      breakUsingUids((0.0, mid()), grain, metric)

    def breakUsingUids(uid: Uid, grain: Double, metric: Metric): Boolean =
      uid == share(uid) { (lead, leadQuery) =>
        distanceCompetition(distanceToSource(uid == lead, metric), leadQuery, uid, grain, metric)
      }

    /** Candidates surrender to the lowest nearby identity; strict `mux` preserves alignment. */
    private def distanceCompetition(
        d: Double,
        leadQuery: () => Uid,
        uid: Uid,
        grain: Double,
        metric: Metric
    ): Uid =
      // Abdicating means advertising an identity nobody can win with.
      val abdicate: Uid = (Double.PositiveInfinity, uid._2)
      mux(d > grain)(uid) {
        mux(d >= 0.5 * grain)(abdicate) {
          minHood(mux(nbr(d) + metric() >= 0.5 * grain)(nbr(abdicate))(leadQuery()))
        }
      }

  def minByFirst[A](a: (Double, A), b: (Double, A)): (Double, A) =
    if (a._1 < b._1) a else b
