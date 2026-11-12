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

import java.util.concurrent.{ExecutionException, ExecutorService, Executors, ScheduledExecutorService, TimeUnit}
import scala.concurrent.duration.*
import scala.util.control.NonFatal

/**
 * Compiles snippets of Scala into live values, in memory, while the runtime runs.
 *
 * One instance keeps one compiler warm: its symbol table is built once and every later compile
 * reuses it, which is the difference between seconds and a fraction of one. The compiler insists
 * that this table only ever be touched by one thread, and asserts it, so every compile -- and the
 * loading of its result, which runs the author's code -- happens on a thread of this instance's
 * own. Callers on any thread simply wait for it; their compiles are served in order.
 *
 * Nothing is written to disk and nothing is loaded unless every check passed. A loaded snippet
 * is metered: whoever calls into it must do so inside [[Fuel.metered]], or it refuses to run.
 *
 * A compile that outlasts `compileTimeout` is cancelled through the compiler's own flag, which
 * makes the typer and implicit search return at once, and its output is discarded: a source can
 * be written to keep a compiler busy, and this one runs in the same JVM as the control loop.
 *
 * @param classpath      what snippets compile against; the running JVM's own by default, which
 *                       is the fat jar in production and the forked test classpath under sbt
 * @param startupBudget  what evaluating the author's top-level expression may spend
 * @param compileTimeout how long one compile may take, the first one's warm-up included
 */
final class FormationCompiler(
    classpath: String = System.getProperty("java.class.path", ""),
    startupBudget: Fuel.Budget = Fuel.Budget.Default,
    compileTimeout: FiniteDuration = FormationCompiler.DefaultCompileTimeout
):

  private val driver = SnippetDriver(classpath)

  /** Confined to [[worker]]: touched by no other thread. */
  private var compiled: Long = 0L

  /** A daemon: it holds nothing that outlives the process, and must not keep it alive. */
  private val worker: ExecutorService = Executors.newSingleThreadExecutor { task =>
    val thread = Thread(task, "formation-compiler")
    thread.setDaemon(true)
    thread
  }

  /** Blocking, from any thread: a warm compile takes tens of milliseconds, a cold one seconds. */
  def compile[A](kind: SnippetKind[A], source: String): Either[List[Problem], A] =
    if source.length > FormationCompiler.MaxSourceLength then
      Left(List(Problem(0, 0, s"the source is longer than ${FormationCompiler.MaxSourceLength} characters")))
    else
      try worker.submit(() => compileWithin(kind, source)).get()
      catch
        case error: ExecutionException => Left(List(Problem(0, 0, s"the compiler failed: ${error.getCause}")))
        case _: InterruptedException =>
          Thread.currentThread().interrupt()
          Left(List(Problem(0, 0, "the compile was interrupted")))

  private def compileWithin[A](kind: SnippetKind[A], source: String): Either[List[Problem], A] =
    compiled += 1
    val wrapped = Wrapped(kind, s"formations.g$compiled", source)
    try compileOn(kind, wrapped)
    catch case NonFatal(error) => Left(List(Problem(0, 0, s"the compiler failed: ${error.getMessage}")))

  private def compileOn[A](kind: SnippetKind[A], wrapped: Wrapped[A]): Either[List[Problem], A] =
    driver.root match
      case None => Left(List(Problem(0, 0, "the formation compiler could not start")))
      case Some(root) =>
        val output = VirtualDirectory("<formation>")
        given Context = root.fresh.setSetting(root.settings.outputDir, output).setReporter(StoreReporter(null))
        val run = SandboxedCompiler(kind.policy).newRun
        val deadline = FormationCompiler.watchdog.schedule(
          (() => run.isCancelled = true): Runnable,
          compileTimeout.toNanos,
          TimeUnit.NANOSECONDS
        )
        val crash =
          try
            run.compileUnits(List(CompilationUnit(SourceFile.virtual(wrapped.fileName, wrapped.text), mustExist = false)))
            None
          catch case NonFatal(error) => Some(error)
          finally deadline.cancel(false)
        val errors = summon[Context].reporter.removeBufferedMessages.filter(_.level == ERROR)
        // Checked first: a cancelled typer leaves wildcard-typed trees, which may compile, may trip
        // a compiler assertion, and are never to be loaded either way.
        if run.isCancelled then Left(List(Problem(0, 0, s"compiling took longer than ${compileTimeout.toMillis} ms")))
        else
          crash match
            case Some(error) => Left(List(Problem(0, 0, s"the compiler failed: ${error.getMessage}")))
            case None if errors.nonEmpty => Left(errors.map(wrapped.problem))
            case None => load(kind, wrapped, output)

  /**
   * Initialising the wrapper runs the author's expression, so it is metered like any later call
   * and its failures are the author's.
   */
  private def load[A](kind: SnippetKind[A], wrapped: Wrapped[A], output: AbstractFile): Either[List[Problem], A] =
    val loader = AbstractFileClassLoader(output, kind.resultClass.getClassLoader)
    try
      Fuel.metered(startupBudget)(Class.forName(wrapped.moduleClass, true, loader).getField("MODULE$").get(null)) match
        case Left(outOfFuel) => Left(List(Problem(0, 0, s"the snippet ran out while starting: ${outOfFuel.getMessage}")))
        case Right(snippet: Snippet[?]) if kind.resultClass.isInstance(snippet.value) =>
          Right(kind.resultClass.cast(snippet.value))
        case Right(_) => Left(List(Problem(0, 0, s"the snippet did not produce a ${kind.resultType}")))
    catch
      case error: ExceptionInInitializerError =>
        Left(List(Problem(0, 0, s"the snippet failed while starting: ${error.getCause}")))
      case NonFatal(error) => Left(List(Problem(0, 0, s"the snippet could not be loaded: $error")))

object FormationCompiler:
  /** Characters; mirrored by the dashboard's `CUSTOM_SOURCE_MAX_LENGTH`. */
  val MaxSourceLength: Int = 8_000

  /** Several times a cold compile, which is the slowest legitimate one at under two seconds. */
  val DefaultCompileTimeout: FiniteDuration = 10.seconds

  /** One daemon thread for every compiler's deadlines: it only ever flips a flag. */
  private[formation] lazy val watchdog: ScheduledExecutorService =
    Executors.newSingleThreadScheduledExecutor { task =>
      val thread = Thread(task, "formation-compile-watchdog")
      thread.setDaemon(true)
      thread
    }

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
