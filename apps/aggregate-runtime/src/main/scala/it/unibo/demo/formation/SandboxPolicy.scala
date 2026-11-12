package it.unibo.demo.formation

/**
 * What a snippet may reference, by fully qualified symbol name.
 *
 * An allowlist, not a denylist: the JVM offers far too many doors (I/O, reflection, threads,
 * processes, class loading) to enumerate the dangerous ones. A prefix admits a whole namespace;
 * `denied` carves exceptions out of one. Types are never checked, only terms: naming a type
 * runs nothing.
 *
 * This is about what a snippet can reach, not how long it runs: termination is not something
 * an allowlist can promise (see FormationCompilerSuite).
 */
final case class SandboxPolicy(allowed: List[String], denied: List[String]):

  def allows(fullName: String, name: String): Boolean =
    !SandboxPolicy.ForbiddenMembers.contains(name)
      && !denied.exists(SandboxPolicy.within(fullName, _))
      && allowed.exists(SandboxPolicy.within(fullName, _))

  def extendedWith(more: String*): SandboxPolicy = copy(allowed = allowed ++ more)

object SandboxPolicy:

  /** Reachable on every object through `Any`/`AnyRef`, and each one a door out of the sandbox. */
  val ForbiddenMembers: Set[String] =
    Set("getClass", "asInstanceOf", "synchronized", "wait", "notify", "notifyAll", "finalize", "clone")

  /** `scala.math` admits `scala.math.sin`, never `scala.mathematics`. */
  def within(fullName: String, prefix: String): Boolean =
    fullName == prefix || fullName.startsWith(prefix + ".")

  /** Arithmetic, immutable data and functions: what a pure computation over numbers needs. */
  private val Core: List[String] =
    List(
      "scala.math",
      "java.lang.Math",
      "scala.Int", "scala.Long", "scala.Double", "scala.Float", "scala.Short", "scala.Byte", "scala.Char",
      "scala.Boolean", "scala.Unit",
      // Their forbidden members are excluded by name above.
      "scala.Any", "scala.AnyRef", "java.lang.Object",
      "scala.runtime.RichInt", "scala.runtime.RichLong", "scala.runtime.RichDouble", "scala.runtime.RichFloat",
      // Predef inherits the numeric wrappers behind `0 until n` and `x.abs` from here.
      "scala.LowPriorityImplicits.intWrapper", "scala.LowPriorityImplicits.longWrapper",
      "scala.LowPriorityImplicits.doubleWrapper", "scala.LowPriorityImplicits.floatWrapper",
      "scala.Predef.identity", "scala.Predef.$conforms", "scala.Predef.ArrowAssoc",
      "scala.Predef.Map", "scala.Predef.Set",
      "scala.<:<", "scala.=:=",
      "scala.Option", "scala.Some", "scala.None",
      "scala.package.List", "scala.package.Nil", "scala.package.::", "scala.package.Seq",
      "scala.package.IndexedSeq", "scala.package.Vector", "scala.package.Range",
      "scala.collection",
      "scala.PartialFunction"
    ) ++ (1 to 22).map(n => s"scala.Tuple$n") ++ (0 to 22).map(n => s"scala.Function$n")

  /** Mutable, lazy or Java-bridging corners of the collections, none of which a layout needs. */
  private val CoreDenied: List[String] = List(
    "scala.collection.mutable",
    "scala.collection.concurrent",
    "scala.collection.convert",
    "scala.collection.JavaConverters",
    "scala.collection.Iterator",
    "scala.collection.immutable.LazyList",
    "scala.collection.immutable.Stream"
  )

  val Geometry: SandboxPolicy = SandboxPolicy(Core :+ "it.unibo.demo.formation.dsl", CoreDenied)

  /** Geometry, plus the field calculus and this runtime's building blocks. */
  val Aggregate: SandboxPolicy = Geometry.extendedWith(
    "it.unibo.scafi.core",
    "it.unibo.scafi.lib",
    "it.unibo.scafi.incarnations",
    "it.unibo.core.aggregate",
    "it.unibo.demo.scenarios",
    "it.unibo.demo.robot.Actuation"
  )
