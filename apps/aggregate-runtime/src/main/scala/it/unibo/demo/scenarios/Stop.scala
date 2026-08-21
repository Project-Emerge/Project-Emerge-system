package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, Rotation}

class Stop() extends BaseDemo:
  override def main(): Actuation =
    mux(sense[Int]("leader") == mid()){
      Actuation.NoOp
    } {
      Actuation.Stop
    }
