package it.unibo.demo

import it.unibo.core.UpdateLoop
import it.unibo.core.aggregate.AggregateIncarnation.*
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.provider.MqttProvider
import it.unibo.demo.robot.RobotUpdateMqtt
import it.unibo.demo.scenarios.{BaseDemo, CircleFormation, LineFormation}
import it.unibo.utils.Position.given
import scalafx.application.JFXApp3
import scalafx.scene.layout.Pane
import scalafx.scene.paint.Color
import view.fx.*

import scala.concurrent.ExecutionContext.Implicits.global
import scala.util.Random

class BaseAggregateServiceExample(demoToLaunch: BaseDemo) extends JFXApp3:
  private val agentsNeighborhoodRadius = 500
  private val nodeGuiSize = 10
  override def start(): Unit =

    val agents = 12
    val provider = MqttProvider("tcp://localhost:1883")
    provider.start()
    val update = RobotUpdateMqtt(0.6)
    val aggregateOrchestrator =
      AggregateOrchestrator[Position, Info, Actuation](
        (0 to agents).toSet,
        demoToLaunch
      )

    val basePane = Pane()
    val guiPane = Pane()
    val neighborhoodPane = Pane()
    basePane.children.addAll(neighborhoodPane, guiPane)
    val worldPane = WorldPanel(guiPane, NodeStyle(nodeGuiSize, nodeGuiSize, Color.Blue))
    val neighbouringPane = NeighborhoodPanel(guiPane, neighborhoodPane)
    val render = SimpleRender(
      worldPane,
      neighbouringPane,
      MagnifierPolicy.translateAndScale((400, 400), 10)
    )

    UpdateLoop.loop(30)(
      provider,
      aggregateOrchestrator,
      update,
      render
    )

    stage = new JFXApp3.PrimaryStage:
      title = "Aggregate Service Example"
      scene = new scalafx.scene.Scene:
        content = basePane
      width = 800
      height = 800

  private def randomAgents(howMany: Int, maxPosition: Int): Map[ID, (Double, Double)] =
    val random = new scala.util.Random
    (1 to howMany).map { i =>
      i -> (random.nextDouble() * maxPosition, random.nextDouble() * maxPosition)
    }.toMap


object LineFormationDemo extends BaseAggregateServiceExample(LineFormation(5, 5, 1, 4.5))

object CircleFormationDemo extends BaseAggregateServiceExample(CircleFormation(15, 5, 1, 2.5))