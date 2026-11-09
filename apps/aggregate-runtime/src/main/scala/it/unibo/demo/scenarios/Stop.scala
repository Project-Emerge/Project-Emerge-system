package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.{Forward, Rotation}

class Stop() extends BaseDemo:
  override def main(): Actuation =
    // The root is left alone rather than halted, so it stays available for manual driving.
    // With no leader picked and none elected, every robot simply stops.
    mux(isRootDevice){
      Actuation.NoOp
    } {
      Actuation.Stop
    }

class NoOp() extends BaseDemo:
  override def main(): Actuation =
    Actuation.NoOp
