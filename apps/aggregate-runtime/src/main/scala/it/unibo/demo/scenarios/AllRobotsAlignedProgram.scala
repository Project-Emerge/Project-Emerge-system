package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation

/**
 * A simple program used to test the framework.
 * All robots will move towards the same direction.
 * 
 */
class AllRobotsAlignedProgram extends BaseDemo:
  override def main(): Actuation = Actuation.Forward(normalize((-1.0, -1.0)))
