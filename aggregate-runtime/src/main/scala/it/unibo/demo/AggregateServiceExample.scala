package it.unibo.demo

import it.unibo.core.UpdateLoop
import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.provider.MqttProvider
import it.unibo.demo.robot.RobotUpdateMqtt
import it.unibo.demo.scenarios.{BaseDemo, CircleFormation, LineFormation}
import it.unibo.utils.Position.given

import scala.concurrent.ExecutionContext.Implicits.global
import scala.util.Random
import it.unibo.core.Boundary
import scala.concurrent.Future
import it.unibo.core.Environment

private final val BROKER_URL = System.getProperty("mqtt.broker.url", "tcp://localhost:1883")

class BaseAggregateServiceExample(demoToLaunch: BaseDemo) extends App:
  private val agentsNeighborhoodRadius = 500
  private val nodeGuiSize = 10

  val agents = 12
  val provider = MqttProvider("tcp://localhost:1883")
  provider.start()
  val update = RobotUpdateMqtt(0.6)
  val aggregateOrchestrator =
    AggregateOrchestrator[Position, Info, Actuation](
      (0 to agents).toSet,
      demoToLaunch
    )

  val render = new Boundary[ID, Position, Info]:
    override def output(environment: Environment[ID, Position, Info]): Future[Unit] =
      Future.successful(())

  UpdateLoop.loop(30)(
    provider,
    aggregateOrchestrator,
    update,
    render
  )

private def randomAgents(howMany: Int, maxPosition: Int): Map[ID, (Double, Double)] =
  val random = new scala.util.Random
  (1 to howMany).map { i =>
    i -> (random.nextDouble() * maxPosition, random.nextDouble() * maxPosition)
  }.toMap

object LineFormationDemo extends BaseAggregateServiceExample(LineFormation(5, 5, 1, 4.5))

object CircleFormationDemo extends BaseAggregateServiceExample(CircleFormation(15, 5, 1, 2.5))

object HeadlessFormation extends App:
  println("Starting headless formation demo...")