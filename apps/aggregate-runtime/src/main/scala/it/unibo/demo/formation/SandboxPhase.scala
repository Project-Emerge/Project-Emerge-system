package it.unibo.demo.formation

import dotty.tools.dotc.ast.tpd
import dotty.tools.dotc.core.Constants.ClazzTag
import dotty.tools.dotc.core.Contexts.{Context, ctx}
import dotty.tools.dotc.core.Flags
import dotty.tools.dotc.core.Phases.Phase
import dotty.tools.dotc.core.Symbols.*
import dotty.tools.dotc.report
import dotty.tools.dotc.typer.TyperPhase

/**
 * Holds every term a snippet references to a [[SandboxPolicy]], straight after typing.
 *
 * After the typer, not before: only typed trees say which symbol a name resolved to, through
 * imports, implicits and inherited members alike. Before inlining and PostTyper, so the trees
 * are still the author's own and not library or synthetic code expanded into them.
 *
 * The whole compilation unit is checked, the wrapper included, so an author who closes the
 * wrapper's braces early gains nothing: what they write outside the expression is checked too.
 */
private[formation] final class SandboxPhase(policy: SandboxPolicy) extends Phase:

  override def phaseName: String = SandboxPhase.Name

  /** Declared so the compiler refuses to assemble a pipeline that would run us on untyped trees. */
  override def runsAfter: Set[String] = Set(TyperPhase.name)

  override def run(using Context): Unit =
    val tree = ctx.compilationUnit.tpdTree
    // Fail closed: an empty tree here means nothing was checked, not that nothing needs checking.
    if tree.isEmpty then report.error("the formation sandbox found no typed code to check")
    else Checker().traverse(tree)

  private final class Checker extends tpd.TreeTraverser:
    override def traverse(tree: tpd.Tree)(using Context): Unit = tree match
      // Naming something runs nothing; using it is checked where it is used.
      case _: tpd.Import => ()
      case _: tpd.WhileDo =>
        report.error("loops are not available in a formation: map over a range instead", tree.srcPos)
      case _: tpd.Export =>
        report.error("export clauses are not available in a formation", tree.srcPos)
      case tpd.Literal(constant) if constant.tag == ClazzTag =>
        report.error("class literals are not available in a formation", tree.srcPos)
      case ref: tpd.RefTree if ref.isTerm =>
        check(ref.symbol, ref)
        traverseChildren(tree)
      case _ => traverseChildren(tree)

  private def check(symbol: Symbol, at: tpd.Tree)(using Context): Unit =
    val unchecked = !symbol.exists || symbol.is(Flags.Package) || symbol.source == ctx.compilationUnit.source
    val qualified = SandboxPhase.sourceName(symbol.fullName.toString)
    if !unchecked && !policy.allows(qualified, symbol.name.toString) then
      report.error(s"`$qualified` is not available in a formation. ${SandboxPhase.Hint}", at.srcPos)

private[formation] object SandboxPhase:
  val Name: String = "formationSandbox"

  /**
   * `scala.Tuple2$.apply` as its author would write it, `scala.Tuple2.apply`: the compiler names a
   * companion's members after its module class. Members keep the owner that defines them, so an
   * inherited one is listed under its defining class.
   */
  def sourceName(fullName: String): String = fullName.replace("$.", ".").stripSuffix("$")

  /** Read by whoever wrote the snippet, often a language model: says what to use instead. */
  val Hint: String =
    "Formations may use scala.math, immutable collections, tuples, Option, functions and the formation DSL."
