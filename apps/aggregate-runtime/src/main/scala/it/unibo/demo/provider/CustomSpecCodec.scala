package it.unibo.demo.provider

import it.unibo.demo.formation.{FormationCompiler, Problem, SnippetKind}
import it.unibo.demo.scenarios.CustomSpec

import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicReference
import scala.util.Try

/** What a `custom` payload's source must evaluate to. */
enum CustomKind(val wire: String):
  /** A `Formation`: geometry only, planned and steered by `CustomFormation`. */
  case Formation extends CustomKind("formation")

  /** A `BaseDemo`: a whole aggregate program, run in place of `CustomFormation`. */
  case Program extends CustomKind("program")

object CustomKind:
  def fromWire(wire: String): Option[CustomKind] = values.find(_.wire == wire)

/** A `custom` payload read but not yet compiled: reading is cheap and total, compiling is neither. */
enum CustomRequest:
  /** An explicit null: forget the current spec. */
  case Clear
  case Compile(kind: CustomKind, source: String)
  case Rejected(reason: String)

/**
 * Reads the optional `custom` object of a `/config/formation` payload, then compiles it.
 *
 * Two steps because they run in different places: reading happens on the MQTT callback, where it
 * must be instant, and compiling on a blocking thread, where it may take a cold second. Neither
 * runs in the 20 Hz control loop, which only ever evaluates what was compiled.
 *
 * Hand-walked rather than derived from a case class, as [[MqttProvider]] does for this topic:
 * `/config/formation` is retained and agent-authored, so it must be lenient. An upickle `read`
 * throws on any deviation, and that failure would take the `program`, `leaderId` and `anchor`
 * update down with it -- on a retained topic, a stuck fleet with no live publisher to correct it.
 *
 * Total by construction: a hostile payload can only ever produce [[CustomRequest.Rejected]], and
 * a hostile source only [[CustomSpec.Invalid]].
 */
object CustomSpecCodec:

  /** The payload key. A sibling of `params`, not an entry in it: `params` stays numbers-only. */
  val Key: String = "custom"

  /**
   * The spec this message asks for, or `None` when it says nothing about one.
   *
   * `None` must leave the previous spec in place -- the same "a message that omits a key changes
   * nothing about that key" semantics `params` already has, and what an operator expects when
   * they switch to `circleShape` and back. An explicit null clears it.
   *
   * Deliberately unlike `leaderId`, which a message without the key does clear: that is
   * documented in [[MqttProvider]] and is about never rooting a gradient on a stale device id.
   */
  def fromCommand(command: ujson.Value): Option[CustomRequest] =
    Try(command.obj.get(Key)).toOption.flatten.map(read)

  /** Never throws; a payload it cannot use becomes [[CustomRequest.Rejected]] with a reason. */
  def read(node: ujson.Value): CustomRequest =
    Try(readUnsafe(node)).getOrElse(CustomRequest.Rejected("custom could not be read"))

  private def readUnsafe(node: ujson.Value): CustomRequest =
    if node.isNull then CustomRequest.Clear
    else
      node.objOpt match
        case None => CustomRequest.Rejected("custom must be a JSON object")
        case Some(fields) =>
          val kinds = CustomKind.values.map(_.wire).mkString(", ")
          fields.get("kind").flatMap(_.strOpt).flatMap(CustomKind.fromWire) match
            case None => CustomRequest.Rejected(s"custom.kind must be one of $kinds")
            case Some(kind) =>
              fields.get("source").flatMap(_.strOpt) match
                case None | Some("") => CustomRequest.Rejected("custom.source must be a non-empty string of Scala")
                case Some(source) if source.length > FormationCompiler.MaxSourceLength =>
                  CustomRequest.Rejected(s"custom.source is longer than ${FormationCompiler.MaxSourceLength} characters")
                case Some(source) => CustomRequest.Compile(kind, source)

  /** Blocking: a warm compile takes tens of milliseconds, a cold one over a second. */
  def compile(compiler: FormationCompiler, request: CustomRequest): CustomSpec = request match
    case CustomRequest.Clear => CustomSpec.Absent
    case CustomRequest.Rejected(reason) => CustomSpec.Invalid(reason)
    case CustomRequest.Compile(CustomKind.Formation, source) =>
      compiler.compile(SnippetKind.Geometry, source).fold(rejection, CustomSpec.Geometry(_))
    case CustomRequest.Compile(CustomKind.Program, source) =>
      compiler.compile(SnippetKind.Aggregate, source).fold(rejection, CustomSpec.Program(programId(source), _))

  /** Every problem on a line of its own, each naming where it is, for the log and the author. */
  def describe(problems: List[Problem]): String =
    problems.map { problem =>
      if problem.line > 0 then s"line ${problem.line}, column ${problem.column}: ${problem.message}"
      else problem.message
    }.mkString("\n")

  private def rejection(problems: List[Problem]): CustomSpec = CustomSpec.Invalid(describe(problems))

  /** Stable across devices and restarts, and short enough to sit in every export's path. */
  private def programId(source: String): String =
    MessageDigest
      .getInstance("SHA-256")
      .digest(source.getBytes(StandardCharsets.UTF_8))
      .take(8)
      .map(byte => f"${byte & 0xff}%02x")
      .mkString

/**
 * [[CustomSpecCodec.compile]], reusing the last spec compiled from the very same source.
 *
 * Republishing is the common case, not the rare one: every slider an operator drags resends the
 * whole command, `custom` included. Recompiling would cost tens of milliseconds each time and,
 * worse, load a fresh class: devices holding the old and the new instance of a designed program
 * would then sit on different `align` keys until every pose had picked up the new config.
 *
 * Only successes are kept. A refusal is cheap to reach again, and one caused by a compile that
 * merely ran out of time deserves another try.
 */
final class CustomSpecCache(compiler: FormationCompiler):
  private val last = AtomicReference[Option[(CustomRequest, CustomSpec)]](None)

  def compile(request: CustomRequest): CustomSpec =
    last.get() match
      case Some((previous, spec)) if previous == request => spec
      case _ =>
        val spec = CustomSpecCodec.compile(compiler, request)
        spec match
          case CustomSpec.Geometry(_) | CustomSpec.Program(_, _) => last.set(Some((request, spec)))
          case _ => ()
        spec
