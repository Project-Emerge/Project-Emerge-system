package it.unibo.utils

import it.unibo.core.DistanceEstimator

/**
 * Represents a position in a 2D space.
 * It is a tuple of two doubles.
 *
 * Moreover, it provides a `DistanceEstimator` instance for `Position`,
 * using for estimate the distance between two positions the Euclidean distance.
 */
object Position:
  type Position = (Double, Double)
  given DistanceEstimator[Position] with
    def distance(p1: Position, p2: Position): Double =
      val (x1, y1) = p1
      val (x2, y2) = p2
      Math.sqrt(Math.pow(x1 - x2, 2) + Math.pow(y1 - y2, 2))

    override def distanceVector(p1: (Double, Double), p2: (Double, Double)): (Double, Double) =
      val (x1, y1) = p1
      val (x2, y2) = p2
      (x2 - x1, y2 - y1)
