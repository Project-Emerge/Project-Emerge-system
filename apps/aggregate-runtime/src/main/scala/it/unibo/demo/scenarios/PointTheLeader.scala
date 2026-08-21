package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, Rotation}

class PointTheLeader() extends BaseDemo:
  override def main(): Actuation =
    align(this.getClass):
      _ => {
        val distance =
          gradientCast[(Double, Double)](
            sense[Int]("leader") == mid(),
            (0.0, 0.0),
            (x, y) => (x + distanceVector._1, y + distanceVector._2)
          )
        mux(sense[Int]("leader") == mid())(Rotation(0, 1))(Rotation(normalize(distance))) // up
      }
