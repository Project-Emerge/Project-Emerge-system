package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, Rotation}

class PointTheLeader() extends BaseDemo:
  override def main(): Actuation =
    align(this.getClass):
      _ => {
        // `isRootDevice` rather than the raw molecule, so that this also works when the
        // operator picked nobody and the swarm elected its own leader.
        val root = isRootDevice
        val distance =
          gradientCast[(Double, Double)](
            root,
            (0.0, 0.0),
            (x, y) => (x + distanceVector._1, y + distanceVector._2)
          )
        mux(root)(Rotation(0, 1))(Rotation(normalize(distance))) // up
      }
