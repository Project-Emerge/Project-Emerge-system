package it.unibo.demo.scenarios

import it.unibo.demo.robot.Actuation
import it.unibo.demo.robot.Actuation.*

class FollowTheLeaderRotating(private val leaderId: Int, private val timeInterval: Long = 10_000L) extends BaseDemo:
  private val directions = List(
    Actuation.Rotation(normalize(0.5, 0.5)),
    Actuation.Rotation(normalize(0.5, -0.5)),
    Actuation.Rotation(normalize(-0.5, -0.5)),
    Actuation.Rotation(normalize(-0.5, 0.5))
  )

  override def main(): Actuation =
    executeOnEach(10_000L, (NoOp, 0)): (_, prevIndex) =>
      (directions(prevIndex % directions.size), prevIndex + 1)
    ._1

  private def executeOnEach[T](deltaTime: Long, default: T)(logic: T => T): T =
    val currentTime = System.currentTimeMillis()
    val result = rep((currentTime, default)): (prevTime, prevValue) =>
      if currentTime - prevTime > deltaTime then (currentTime, logic(prevValue))
      else (prevTime, prevValue)
    result._2
