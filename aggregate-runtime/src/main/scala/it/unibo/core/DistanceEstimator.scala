package it.unibo.core

/**
 * A type class used to estimate the distance between two positions.
 * @tparam Position the type of the position (e.g., a 2D point, a 3D point, etc.)
 */
trait DistanceEstimator[Position]:
  /**
   * Estimates the distance between two positions.
   * e.g., if p1 = (1, 1) and p2 = (3, 3), the distance is sqrt(2^2 + 2^2) = 2.83 (euclidean distance)
   * @param p1 the first position
   * @param p2 the second position
   * @return the distance between the two positions
   */
  def distance(p1: Position, p2: Position): Double

  /**
   * Estimates the distance vector between two positions.
   * e.g., if p1 = (1, 1) and p2 = (3, 3), the distance vector is (2, 2)
   * @param p1 the first position
   * @param p2 the second position
   * @return the distance vector between the two positions
   */
  def distanceVector(p1: Position, p2: Position): Position

object DistanceEstimator:
  /**
   * Extension method to estimate the distance between two positions.
   */
  extension [P: DistanceEstimator](position: P)
    def distance(other: P): Double = summon[DistanceEstimator[P]].distance(position, other)
    def distanceVector(other: P): P = summon[DistanceEstimator[P]].distanceVector(position, other)
