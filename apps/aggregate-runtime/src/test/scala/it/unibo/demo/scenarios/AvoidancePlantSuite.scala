package it.unibo.demo.scenarios

import it.unibo.core.Environment
import it.unibo.core.aggregate.AggregateOrchestrator
import it.unibo.demo.robot.*
import it.unibo.utils.Position.given

/** The full formation field and physical drive loop, without a broker or wall-clock sleeps. */
class AvoidancePlantSuite extends munit.FunSuite:
  private type Position = (Double, Double)
  private val config = DriveConfig.fromEnvironment
  private val gains = ControlGains()

  // Frozen pre-detour steering for a trajectory comparison, with the same assignment and drive
  // controller as production. This catches "fixes" that merely hide the oscillation by stopping.
  private class RadialBaseline extends VerticalLineFormation():
    override private[scenarios] def actuate(frame: AnchorFrame, displacement: Position): Actuation =
      val radius = sense[Double](BaseDemo.CollisionArea)
      val sum = neighbourVectors.values.foldLeft((0.0, 0.0)) { (sum, p) =>
        val d = module(p)
        val weight = if d < 1e-9 || d >= radius then 0.0 else 0.6 * (1.0 - d / radius) / (d * d)
        val unit = normalize(p)
        (sum._1 - unit._1 * weight, sum._2 - unit._2 * weight)
      }
      val scale = if module(sum) > 2.0 then 2.0 / module(sum) else 1.0
      val avoidance = (sum._1 * scale, sum._2 * scale)
      if module(displacement) >= sense[Double](BaseDemo.StabilityThreshold) then
        Actuation.Forward(normalize((displacement._1 + avoidance._1, displacement._2 + avoidance._2)), module(displacement))
      else if frame.isRoot then Actuation.NoOp
      else
        val reference = DifferentialDrive.headingVector(frame.reference, config)
        if module(avoidance) > 0.01 then
          Actuation.Forward(normalize((reference._1 + avoidance._1, reference._2 + avoidance._2)),
            math.min(1.0, module(avoidance) / 2.0) * radius)
        else Actuation.Rotation(reference)

  private case class Journey(
      arrival: Option[Double],
      finalDistance: Double,
      minimumSeparation: Double,
      sharpDirectionChanges: Int,
      rotation: Double,
      tailTravel: Double,
      finalHeadingError: Double
  )

  private def run(program: BaseDemo, initialHeading: Double, noiseM: Double): Journey =
    val loop = AggregateOrchestrator[Position, Actuation](program)
    val plant = RobotPlant(config, 0.0, 0.7, initialHeading - config.markerYawOffsetRad)
    val destination = (0.0, -0.4)
    var state = ControlState.initial
    var desired = Option.empty[(Double, Double)]
    var shadow = (0.0, 0.0)
    var nextControl = 0.0
    var nextPublish = 0.0
    var time = 0.0
    var rounds = 0
    var arrival = Option.empty[Double]
    var minimumSeparation = Double.PositiveInfinity
    var previousDirection = Option.empty[Double]
    var sharpChanges = 0
    var rotation = 0.0
    var tailTravel = 0.0
    val controlPeriod = 0.05
    val publishPeriod = MotorCommandPublisher.defaultPeriod.toNanos / 1e9
    val microStep = 0.002

    while time < 30.0 do
      if time >= nextControl then
        nextControl += controlPeriod
        rounds += 1
        val observed = (
          plant.x + noiseM * math.sin(rounds * 1.7),
          plant.y + noiseM * math.cos(rounds * 2.3)
        )
        val world = new Environment[Int, Position, Map[String, Any]]:
          override def nodes = Set(0, 1)
          override def position(id: Int) = if id == 0 then (0.0, 0.0) else observed
          override def neighbors(id: Int) = nodes
          override def sensing(id: Int): Map[String, Any] = FormationDefaults.All ++ Map(
            // The radius this journey was measured at, not the fleet's default. The slot at 0.4
            // is exactly the clearance 0.3 + 2 * 0.05 allows.
            BaseDemo.CollisionArea -> 0.3,
            BaseDemo.StabilityThreshold -> 0.05,
            VerticalLineFormation.INTER_DISTANCE_SENSING -> 0.4,
            BaseDemo.Leader -> 0,
            BaseDemo.Anchor -> BaseDemo.AnchorLeader,
            BaseDemo.Orientation -> (if id == 0 then -math.Pi / 2 - config.markerYawOffsetRad else plant.markerYaw)
          )
        val actuations = loop.tick(world)
        assertEquals(actuations.keySet, world.nodes, "both aggregate rounds must succeed")
        assertEquals(actuations(0), Actuation.NoOp, "the anchor must stay fixed")
        val twist = actuations(1) match
          case Actuation.Forward(direction, distance) =>
            val angle = math.atan2(direction._2, direction._1)
            if previousDirection.exists(previous => math.abs(DifferentialDrive.normalizeAngle(angle - previous)) > math.Pi / 2) then
              sharpChanges += 1
            previousDirection = Some(angle)
            val (command, next) = HeadingController.step(state, controlPeriod, plant.heading,
              angle, distance, translate = true, allowReverse = true, config, gains)
            state = next
            command
          case Actuation.Rotation(direction) =>
            previousDirection = None
            val (command, next) = HeadingController.step(state, controlPeriod, plant.heading,
              math.atan2(direction._2, direction._1), 0.0, translate = false, allowReverse = false, config, gains)
            state = next
            command
          case _ =>
            previousDirection = None
            state = ControlState.initial
            Twist.still
        desired = Option.unless(twist.isStill)(DifferentialDrive.toWheels(twist.linearMs, twist.angularRadS, config))
          .filterNot(_ == (0.0, 0.0))

      if time >= nextPublish then
        nextPublish += publishPeriod
        desired match
          case None =>
            shadow = (0.0, 0.0)
            plant.receiveStop()
          case Some((left, right)) =>
            val alpha = config.firmwareEmaAlpha
            val outLeft = FirmwareLagCompensator.command(shadow._1, left, alpha)
            val outRight = FirmwareLagCompensator.command(shadow._2, right, alpha)
            shadow = (FirmwareLagCompensator.advance(shadow._1, outLeft, alpha),
              FirmwareLagCompensator.advance(shadow._2, outRight, alpha))
            plant.receiveMove(outLeft, outRight)

      val previous = (plant.x, plant.y, plant.heading)
      plant.integrate(microStep)
      time += microStep
      rotation += math.abs(DifferentialDrive.normalizeAngle(plant.heading - previous._3))
      if time > 25.0 then tailTravel += math.hypot(plant.x - previous._1, plant.y - previous._2)
      minimumSeparation = math.min(minimumSeparation, math.hypot(plant.x, plant.y))
      if arrival.isEmpty && plant.distanceTo(destination._1, destination._2) < 0.1 then arrival = Some(time)

    Journey(arrival, plant.distanceTo(destination._1, destination._2), minimumSeparation,
      sharpChanges, rotation, tailTravel, math.abs(DifferentialDrive.normalizeAngle(plant.heading + math.Pi / 2)))

  test("the aggregate detour reduces direction reversals in the physical 20 Hz loop") {
    val baseline = run(RadialBaseline(), -math.Pi / 2, noiseM = 0.0)
    val detour = run(VerticalLineFormation(), -math.Pi / 2, noiseM = 0.0)
    println(s"AVOIDANCE radial=$baseline detour=$detour")
    assert(detour.sharpDirectionChanges < baseline.sharpDirectionChanges, s"$baseline -> $detour")
    assert(detour.arrival.exists(_ < 20.0), s"the robot did not reach its slot: $detour")
    assert(detour.finalDistance < 0.11)
    assert(detour.minimumSeparation > 0.2, s"the robot cut through the obstacle: $detour")
    assertEqualsDouble(detour.tailTravel, 0.0, 1e-6)
    assert(detour.finalHeadingError < gains.alignToleranceRad)
  }

  test("pose noise and different initial headings still converge without repeated reversals") {
    List(-math.Pi / 2, 0.0, math.Pi / 2).foreach { heading =>
      val journey = run(VerticalLineFormation(), heading, noiseM = 0.005)
      println(s"AVOIDANCE noisy heading=$heading $journey")
      assert(journey.arrival.exists(_ < 20.0), s"no arrival: $journey")
      assert(journey.finalDistance < 0.11)
      assert(journey.minimumSeparation > 0.2)
      assertEquals(journey.sharpDirectionChanges, 0)
      assert(journey.rotation < 4 * math.Pi, s"the robot kept turning: $journey")
      assertEqualsDouble(journey.tailTravel, 0.0, 1e-6)
      assert(journey.finalHeadingError < gains.alignToleranceRad)
    }
  }
