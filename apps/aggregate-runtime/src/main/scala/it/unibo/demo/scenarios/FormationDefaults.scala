package it.unibo.demo.scenarios

/** Default values for every molecule sensed by a registered program.
  *
  * Missing molecules make `sense` throw, stopping that robot; retained formation messages may
  * also lack parameters added later. Shared with tests to keep defaults from drifting.
  */
object FormationDefaults:

  /**
   * The starting configuration, merged in the order the shapes were added.
   *
   * Deliberately `Map[String, Any]` and not `Map[String, Double]`: [[CustomFormation]] seeds a
   * compiled [[CustomSpec]] here, and `BaseDemo.Anchor` seeds a `String`.
   */
  val All: Map[String, Any] = Map(
    BaseDemo.Program -> "pointToLeader",
    BaseDemo.Leader -> BaseDemo.NoLeader,
    BaseDemo.CollisionArea -> 0.3,
    BaseDemo.StabilityThreshold -> 0.1
  ) ++ BaseDemo.Defaults
    ++ LineFormation.DEFAULTS
    ++ VFormation.DEFAULTS
    ++ VerticalLineFormation.DEFAULTS
    ++ CircleFormation.DEFAULTS
    ++ SquareFormation.DEFAULTS
    ++ HeartFormation.DEFAULTS
    ++ ShapeFormation.DEFAULTS
    ++ CustomFormation.DEFAULTS

  /**
   * The name every program is registered under, paired with the program itself.
   *
   * Shared with the entry point so that a test exercising "every program" cannot fall behind the
   * fleet's actual repertoire, and so the dashboard's `FORMATION_PROGRAMS` has exactly one
   * counterpart to be checked against.
   */
  val Programs: Seq[(String, BaseDemo)] = Seq(
    "pointToLeader" -> PointTheLeader(),
    "vShape" -> VFormation(),
    "squareShape" -> SquareFormation(),
    "circleShape" -> CircleFormation(),
    "lineShape" -> LineFormation(),
    "verticalLineShape" -> VerticalLineFormation(),
    "heartShape" -> HeartFormation(),
    // Time-varying shapes, driven by the shared clock.
    "orbitCircle" -> OrbitFormation(),
    "breathingCircle" -> BreathingCircleFormation(),
    "ringWave" -> RingWaveFormation(),
    "sineLine" -> SineLineFormation(),
    // Geometry supplied as data on /config/formation rather than compiled in.
    "custom" -> CustomFormation(),
    "stop" -> Stop()
  )
