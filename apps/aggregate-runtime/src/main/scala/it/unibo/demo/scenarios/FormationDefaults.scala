package it.unibo.demo.scenarios

/**
 * Every molecule a registered program can `sense`, with the value it takes before an operator
 * has said otherwise.
 *
 * This exists because `sense` throws `SensorUnknownException` on a molecule that is missing, and
 * a thrown round becomes `Actuation.Stop` for that robot -- so a program reading a parameter
 * nobody seeded halts the fleet rather than misbehaving visibly. A retained
 * `/config/formation` published before a parameter existed will not carry it, which makes these
 * defaults load-bearing rather than cosmetic.
 *
 * It lives here, rather than as a local `val` in the entry point, because the same map is needed
 * by the aggregate test suites: three copies drifted apart every time a formation gained a
 * parameter, and the failure surfaced as a halted fleet in a test unrelated to the change.
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
