package it.unibo.demo.formation

/**
 * What a snippet may reference, by fully qualified symbol name.
 *
 * An allowlist, not a denylist: the JVM offers far too many doors (I/O, reflection, threads,
 * processes, class loading) to enumerate the dangerous ones. A prefix admits a whole namespace;
 * `denied` carves exceptions out of one. Types are never checked, only terms: naming a type
 * runs nothing.
 *
 * It also keeps [[Fuel]] honest. A library call costs one step whatever it does inside, so the
 * calls that build something large out of a number alone, with no callback for the meter to
 * see, are refused here; that list is the known ones, not a proof.
 */
final case class SandboxPolicy(allowed: List[String], denied: List[String]):

  /**
   * @param fullName as the compiler spells it: `scala.Tuple2$.apply` for a companion's member,
   *                 which `denied` may name to tell a companion from its class
   * @param name     the member's simple name
   */
  def allows(fullName: String, name: String): Boolean =
    val qualified = SandboxPolicy.sourceName(fullName)
    !SandboxPolicy.ForbiddenMembers.contains(name)
      && !denied.exists(prefix => SandboxPolicy.within(qualified, prefix) || SandboxPolicy.within(fullName, prefix))
      && allowed.exists(SandboxPolicy.within(qualified, _))

  def extendedWith(more: String*): SandboxPolicy = copy(allowed = allowed ++ more)

object SandboxPolicy:

  /**
   * Refused on any owner. The first group is reachable on every object and each one is a door
   * out of the sandbox; the second builds collections far larger than its input, unmetered.
   */
  val ForbiddenMembers: Set[String] = Set(
    "getClass", "asInstanceOf", "synchronized", "wait", "notify", "notifyAll", "finalize", "clone",
    "range", "padTo", "permutations", "combinations", "subsets", "inits", "sliding", "flatten", "transpose",
    "mkString", "addString"
  )

  /**
   * `scala.Tuple2$.apply` as its author would write it, `scala.Tuple2.apply`: the compiler names a
   * companion's members after its module class. Members keep the owner that defines them, so an
   * inherited one is listed under its defining class.
   */
  def sourceName(fullName: String): String = fullName.replace("$.", ".").stripSuffix("$")

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
      // Predef inherits the numeric wrappers behind `x.abs` and `x.max(y)` from here.
      "scala.LowPriorityImplicits.intWrapper", "scala.LowPriorityImplicits.longWrapper",
      "scala.LowPriorityImplicits.doubleWrapper", "scala.LowPriorityImplicits.floatWrapper",
      "scala.Predef.identity", "scala.Predef.$conforms", "scala.Predef.ArrowAssoc",
      "scala.Predef.Map", "scala.Predef.Set",
      "scala.<:<", "scala.=:=",
      "scala.Option", "scala.Some", "scala.None",
      "scala.package.List", "scala.package.Nil", "scala.package.::", "scala.package.Seq",
      "scala.package.IndexedSeq", "scala.package.Vector",
      "scala.collection",
      "scala.PartialFunction"
    ) ++ (1 to 22).map(n => s"scala.Tuple$n") ++ (0 to 22).map(n => s"scala.Function$n")

  private val CoreDenied: List[String] = List(
    // Mutable, lazy or Java-bridging corners of the collections, none of which a layout needs.
    "scala.collection.mutable",
    "scala.collection.concurrent",
    "scala.collection.convert",
    "scala.collection.JavaConverters",
    "scala.collection.Iterator",
    "scala.collection.immutable.LazyList",
    "scala.collection.immutable.Stream",
    // A number in, that many elements or bits out, and nothing metered in between. The DSL's
    // `until` and `to` replace the standard ones; `Range$` is the companion, not its instances.
    "scala.runtime.RichInt.until",
    "scala.runtime.RichInt.to",
    "scala.collection.immutable.Range$",
    "scala.collection.immutable.NumericRange",
    "scala.collection.immutable.BitSet",
    "scala.collection.BitSet",
    "scala.math.BigInt",
    "scala.math.BigDecimal"
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
