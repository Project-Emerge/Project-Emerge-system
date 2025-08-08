package it.unibo.demo.scenarios

import it.unibo.core.aggregate.AggregateIncarnation.{AggregateProgram, BlockG, StandardSensors}

class Program extends AggregateProgram with StandardSensors with BlockG:
  def distanceVector: (Double, Double) = nbrvar[(Double, Double)](NBR_VECTOR)
  private val neighborsMinDistance = 10.0

  def module(position: (Double, Double)): Double = Math.sqrt(position._1 * position._1 + position._2 * position._2)
  def normalize(position: (Double, Double)): (Double, Double) = {
    val module = this.module(position)
    (position._1 / module, position._2 / module)
  }
  def rotate90(position: (Double, Double)): (Double, Double) = (-position._2, position._1)
  /**
   * A simple program in which the nodes go towards the center of a the node with the id 1.
   * @return the vector that the node has to follow to reach the center of the node with id 1.
   */
  override def main(): (Double, Double) =
    val versor = gradientCast[(Double, Double)](mid() == 0, (0.0, 0.0), (x, y) => (-distanceVector._1, -distanceVector._2))
    mux(mid() == 0)((0.0, 0.0))(rotate90(normalize(versor)))
    