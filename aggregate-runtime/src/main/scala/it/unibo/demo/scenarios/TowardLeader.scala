package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, Rotation}

class TowardLeader(leader: Int) extends BaseDemo:
  def rotate270(position: (Double, Double)): (Double, Double) =
    (position._2, -position._1)
  def rotate180(position: (Double, Double)): (Double, Double) =
    (-position._1, -position._2)
  override def main(): Actuation =
    val distance =
      gradientCast[(Double, Double)](
        leader == mid(),
        (0.0, 0.0),
        (x, y) => (x + distanceVector._1, y + distanceVector._2)
      )
    mux(leader == mid())(Rotation(1, 0))(Forward(normalize(distance)))
