package it.unibo.demo.formation

import dotty.tools.dotc.ast.tpd
import dotty.tools.dotc.ast.tpd.TreeOps
import dotty.tools.dotc.core.Constants.ClazzTag
import dotty.tools.dotc.core.Contexts.{Context, ctx}
import dotty.tools.dotc.core.Flags
import dotty.tools.dotc.core.Names.termName
import dotty.tools.dotc.core.Phases.Phase
import dotty.tools.dotc.core.Symbols.*
import dotty.tools.dotc.core.Types.{ExprType, MethodType}
import dotty.tools.dotc.report
import dotty.tools.dotc.typer.TyperPhase
import dotty.tools.dotc.util.Spans.Span

/**
 * Holds a snippet to a [[SandboxPolicy]], then meters it with [[Fuel]], straight after typing.
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
    val unit = ctx.compilationUnit
    // Fail closed: an empty tree here means nothing was checked, not that nothing needs checking.
    if unit.tpdTree.isEmpty then report.error("the formation sandbox found no typed code to check")
    else
      Checker().traverse(unit.tpdTree)
      if !ctx.reporter.hasErrors then unit.tpdTree = Metering().transform(unit.tpdTree)

  private final class Checker extends tpd.TreeTraverser:
    override def traverse(tree: tpd.Tree)(using Context): Unit = tree match
      // Naming something runs nothing; using it is checked where it is used.
      case _: tpd.Import => ()
      case _: tpd.WhileDo =>
        report.error("loops are not available in a formation: map over a range instead", tree.srcPos)
      case _: tpd.Try =>
        report.error("try/catch is not available in a formation: it could swallow running out of fuel", tree.srcPos)
      case _: tpd.Export =>
        report.error("export clauses are not available in a formation", tree.srcPos)
      case tpd.Literal(constant) if constant.tag == ClazzTag =>
        report.error("class literals are not available in a formation", tree.srcPos)
      case definition: tpd.DefDef if definition.symbol.is(Flags.Inline) =>
        // Inlining copies the body as typed, before the meter is added to it.
        report.error("inline definitions are not available in a formation: the fuel meter cannot see into them", tree.srcPos)
      case ref: tpd.RefTree if ref.isTerm =>
        check(ref.symbol, ref)
        traverseChildren(tree)
      case _ => traverseChildren(tree)

  private def check(symbol: Symbol, at: tpd.Tree)(using Context): Unit =
    val unchecked = !symbol.exists || symbol.is(Flags.Package) || symbol.source == ctx.compilationUnit.source
    val fullName = symbol.fullName.toString
    if !unchecked && !policy.allows(fullName, symbol.name.toString) then
      report.error(
        s"`${SandboxPolicy.sourceName(fullName)}` is not available in a formation. ${SandboxPhase.Hint}",
        at.srcPos
      )

  /**
   * One [[Fuel.tick]] at the top of every method and lambda body, and around every by-name
   * argument. Any loop a snippet can write is a recursion or a callback, so each turn of it passes
   * through one of these. Constructors are left alone: a class body only reruns by recursion.
   */
  private final class Metering(using Context) extends tpd.TreeMap:
    private val fuel: Symbol = requiredModule("it.unibo.demo.formation.Fuel")

    private def metered(body: tpd.Tree): tpd.Tree =
      val at = if body.span.exists then body.span.startPos else Span(0)
      tpd.Block(List(tpd.ref(fuel).select(termName("tick")).appliedToNone.withSpan(at)), body)

    override def transform(tree: tpd.Tree)(using Context): tpd.Tree = tree match
      case definition: tpd.DefDef if !definition.rhs.isEmpty && !definition.symbol.isConstructor =>
        cpy.DefDef(definition)(rhs = metered(transform(definition.rhs)))
      case application: tpd.Apply =>
        val arguments = transform(application.args)
        cpy.Apply(application)(transform(application.fun), byNameMetered(application.fun, arguments))
      case _ => super.transform(tree)

    /** By-name arguments run once per use, `List.fill`'s element once per element. */
    private def byNameMetered(function: tpd.Tree, arguments: List[tpd.Tree]): List[tpd.Tree] =
      function.tpe.widen match
        case method: MethodType if method.paramInfos.sizeIs == arguments.size =>
          arguments.zip(method.paramInfos).map {
            case (argument, _: ExprType) => metered(argument)
            case (argument, _) => argument
          }
        case _ => arguments

private[formation] object SandboxPhase:
  val Name: String = "formationSandbox"

  /** Read by whoever wrote the snippet, often a language model: says what to use instead. */
  val Hint: String =
    "Formations may use scala.math, immutable collections, tuples, Option, functions and the formation DSL."
