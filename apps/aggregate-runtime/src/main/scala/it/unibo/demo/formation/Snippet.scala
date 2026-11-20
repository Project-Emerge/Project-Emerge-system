package it.unibo.demo.formation

import it.unibo.demo.formation.dsl.Formation
import it.unibo.demo.scenarios.BaseDemo

/** What every compiled snippet is wrapped into, so the runtime can fetch its value without casts. */
trait Snippet[+A]:
  def value: A

/**
 * One kind of source the runtime accepts.
 *
 * @param resultType  the type the author's expression must have, fully qualified
 * @param resultClass its runtime class, to check the loaded value against
 * @param imports     what the author sees without importing it
 * @param policy      what the author's code may reference
 */
final case class SnippetKind[A](
    resultType: String,
    resultClass: Class[A],
    imports: List[String],
    policy: SandboxPolicy
)

object SnippetKind:
  /** A pure layout of slots: the G/C/G plan and the assignment stay compiled in. */
  val Geometry: SnippetKind[Formation] = SnippetKind(
    "it.unibo.demo.formation.dsl.Formation",
    classOf[Formation],
    List("it.unibo.demo.formation.dsl.*"),
    SandboxPolicy.Geometry
  )

  /** A whole aggregate program, `rep`, `nbr` and `share` included. */
  val Aggregate: SnippetKind[BaseDemo] = SnippetKind(
    "it.unibo.demo.scenarios.BaseDemo",
    classOf[BaseDemo],
    List("it.unibo.demo.scenarios.*", "it.unibo.demo.robot.Actuation"),
    SandboxPolicy.Aggregate
  )

/**
 * Something the author has to fix.
 *
 * @param line   1-based, in the author's own source; 0 when the problem has no position there
 * @param column 1-based; 0 alongside a line of 0
 */
final case class Problem(line: Int, column: Int, message: String)
