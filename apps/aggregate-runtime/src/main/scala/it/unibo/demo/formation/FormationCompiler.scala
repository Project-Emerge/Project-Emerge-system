package it.unibo.demo.formation

import dotty.tools.dotc.{CompilationUnit, Compiler, Driver}
import dotty.tools.dotc.core.Contexts.{Context, inContext}
import dotty.tools.dotc.core.Phases.Phase
import dotty.tools.dotc.interfaces.Diagnostic.ERROR
import dotty.tools.dotc.reporting.{Diagnostic, StoreReporter}
import dotty.tools.dotc.typer.TyperPhase
import dotty.tools.dotc.util.SourceFile
import dotty.tools.io.{AbstractFile, VirtualDirectory}
import dotty.tools.repl.AbstractFileClassLoader

import scala.util.control.NonFatal

/**
 * Compiles snippets of Scala into live values, in memory, while the runtime runs.
 *
 * One instance keeps one compiler warm: its symbol table is built once and every later compile
 * reuses it, which is the difference between seconds and a fraction of one. Compiles are
 * serialised, since that table is not thread-safe.
 *
 * Nothing is written to disk and nothing is loaded unless every check passed. What a loaded
 * snippet then does at run time is not bounded here: see [[SandboxPolicy]].
 *
 * @param classpath what snippets compile against; the running JVM's own by default, which is
 *                  the fat jar in production and the forked test classpath under sbt
 */
final class FormationCompiler(classpath: String = System.getProperty("java.class.path", "")):

  private val driver = SnippetDriver(classpath)
  private var compiled: Long = 0L

  def compile[A](kind: SnippetKind[A], source: String): Either[List[Problem], A] = synchronized {
    compiled += 1
    val wrapped = Wrapped(kind, s"formations.g$compiled", source)
    driver.root match
      case None => Left(List(Problem(0, 0, "the formation compiler could not start")))
      case Some(root) =>
        val output = VirtualDirectory("<formation>")
        given Context = root.fresh.setSetting(root.settings.outputDir, output).setReporter(StoreReporter(null))
        try
          SandboxedCompiler(kind.policy).newRun
            .compileUnits(List(CompilationUnit(SourceFile.virtual(wrapped.fileName, wrapped.text), mustExist = false)))
          val errors = summon[Context].reporter.removeBufferedMessages.filter(_.level == ERROR)
          if errors.nonEmpty then Left(errors.map(wrapped.problem))
          else load(kind, wrapped, output)
        catch case NonFatal(error) => Left(List(Problem(0, 0, s"the compiler failed: ${error.getMessage}")))
  }

  /** Initialising the wrapper runs the author's expression, so its failures are the author's. */
  private def load[A](kind: SnippetKind[A], wrapped: Wrapped[A], output: AbstractFile): Either[List[Problem], A] =
    val loader = AbstractFileClassLoader(output, kind.resultClass.getClassLoader)
    try
      Class.forName(wrapped.moduleClass, true, loader).getField("MODULE$").get(null) match
        case snippet: Snippet[?] if kind.resultClass.isInstance(snippet.value) =>
          Right(kind.resultClass.cast(snippet.value))
        case _ => Left(List(Problem(0, 0, s"the snippet did not produce a ${kind.resultType}")))
    catch
      case error: ExceptionInInitializerError =>
        Left(List(Problem(0, 0, s"the snippet failed while starting: ${error.getCause}")))
      case _: StackOverflowError => Left(List(Problem(0, 0, "the snippet recursed without end")))
      case NonFatal(error) => Left(List(Problem(0, 0, s"the snippet could not be loaded: $error")))

/** The author's expression inside the scaffolding that gives it a name and a type. */
private final case class Wrapped[A](kind: SnippetKind[A], packageName: String, source: String):
  private val prefix: String =
    s"""|package $packageName {
        |${kind.imports.map(i => s"import $i").mkString("\n")}
        |object Designed extends it.unibo.demo.formation.Snippet[${kind.resultType}] {
        |val value: ${kind.resultType} = {
        |""".stripMargin

  /** On its own line, so a snippet ending in a line comment cannot comment the wrapper out. */
  private val suffix: String = "\n}\n}\n}\n"

  private val prefixLines: Int = prefix.count(_ == '\n')
  private val sourceLines: Int = source.count(_ == '\n') + 1

  val text: String = prefix + source + suffix
  val fileName: String = s"$packageName.scala"
  val moduleClass: String = s"$packageName.Designed$$"

  /** Positions moved from the wrapped file into the author's own lines. */
  def problem(diagnostic: Diagnostic): Problem =
    val line = if diagnostic.pos.exists then diagnostic.pos.line - prefixLines + 1 else 0
    if line >= 1 && line <= sourceLines then Problem(line, diagnostic.pos.column + 1, diagnostic.message)
    else Problem(0, 0, diagnostic.message)

/** Settings parsed and the classpath indexed once, then shared by every compile. */
private final class SnippetDriver(classpath: String) extends Driver:
  override protected def sourcesRequired: Boolean = false

  /**
   * No import suggestions: on a type error they scan the whole fat jar for an import that might
   * help, which took over a second per error, and the sandbox refuses nearly all of them anyway.
   */
  private val options =
    Array("-classpath", classpath, "-color:never", "-nowarn", "-Ximport-suggestion-timeout", "0")

  lazy val root: Option[Context] =
    setup(options, initCtx.fresh).map { (_, context) =>
      inContext(context)(context.initialize())
      context
    }

/** The standard pipeline, with the sandbox spliced in straight after the typer. */
private final class SandboxedCompiler(policy: SandboxPolicy) extends Compiler:
  override protected def frontendPhases: List[List[Phase]] =
    val phases = super.frontendPhases
    val typer = phases.indexWhere(_.exists {
      case _: TyperPhase => true
      case _ => false
    })
    val (upToTyper, rest) = phases.splitAt(typer + 1)
    upToTyper ::: List(SandboxPhase(policy)) :: rest
