package it.unibo.demo.provider

import it.unibo.demo.formation.{FormationCompiler, Problem, SnippetKind}
import it.unibo.demo.formation.dsl.{Formation, FormationContext, Point}
import it.unibo.demo.scenarios.{BaseDemo, CustomFormation, CustomSlots, FormationDefaults, ShapeFormation}

import scala.util.Try

/**
 * What an author runs before publishing: compile the source and, for a geometry, lay it out for
 * the fleet that is actually there, at every phase of its cycle, under the same budget the
 * runtime will hold it to. Every way it could fail on the floor is reported here instead.
 *
 * The request/response pair is not retained and carries its own id, so several authors -- or one
 * agent retrying -- can check at once without reading each other's answers.
 */
object FormationCheck:

  val RequestTopic: String = "/formation/compile"

  def responseTopic(id: String): String = s"$RequestTopic/$id"

  /** Nothing an MQTT topic level could misread: no `/`, `+` or `#`. */
  private val IdPattern = "^[A-Za-z0-9_-]{1,64}$".r

  /** Robots to lay out for when the request does not say, and the most it may ask for. */
  val DefaultSlots: Int = 6
  val MaxSlots: Int = 64

  /**
   * @param slots  how many robots besides the anchor, which is what `ctx.count` will be
   * @param params the operator's live parameters, over the shipped defaults
   */
  final case class Request(id: String, kind: CustomKind, source: String, slots: Int, params: Map[String, Double])

  /** `preview` holds the slots at rest, inside the safety envelope; only for a clean geometry. */
  final case class Result(id: String, problems: List[Problem], preview: Option[List[Point]]):
    def ok: Boolean = problems.isEmpty

  def parse(payload: ujson.Value): Either[String, Request] =
    Try(payload.obj).toOption.toRight("a compile request must be a JSON object").flatMap { fields =>
      for
        id <- fields.get("id").flatMap(_.strOpt).filter(IdPattern.matches).toRight("id must be 1 to 64 of A-Z, a-z, 0-9, - and _")
        kind <- fields.get("kind").flatMap(_.strOpt).flatMap(CustomKind.fromWire).toRight("kind must be formation or program")
        source <- fields.get("source").flatMap(_.strOpt).filter(_.nonEmpty).toRight("source must be a non-empty string")
      yield
        val slots = fields.get("slots").flatMap(_.numOpt).filter(_.isFinite).map(_.toInt).getOrElse(DefaultSlots)
        val params = fields.get("params").flatMap(_.objOpt)
          .map(_.flatMap((key, value) => value.numOpt.filter(_.isFinite).map(key -> _)).toMap)
          .getOrElse(Map.empty[String, Double])
        Request(id, kind, source, math.min(MaxSlots, math.max(1, slots)), params)
    }

  /** Blocking, for as long as one compile and one metered layout take. */
  def run(compiler: FormationCompiler, request: Request): Result = request.kind match
    case CustomKind.Program =>
      compiler.compile(SnippetKind.Aggregate, request.source).fold(
        problems => Result(request.id, problems, None),
        _ => Result(request.id, Nil, None)
      )
    case CustomKind.Formation =>
      compiler.compile(SnippetKind.Geometry, request.source).fold(
        problems => Result(request.id, problems, None),
        formation => laidOut(request, formation)
      )

  private def laidOut(request: Request, formation: Formation): Result =
    val setting = (name: String) => request.params.get(name).orElse(FormationDefaults.All.get(name).collect { case value: Double => value })
    val count = request.slots
    val layout = CustomFormation.layout(
      formation,
      count,
      setting,
      setting(CustomFormation.SCALE_SENSING).getOrElse(1.0),
      setting(CustomFormation.MAX_RADIUS_SENSING).getOrElse(CustomSlots.AbsoluteMaxRadius),
      ShapeFormation.clearance(
        setting(BaseDemo.CollisionArea).getOrElse(0.0),
        setting(BaseDemo.StabilityThreshold).getOrElse(0.0)
      )
    )
    val outcome = CustomFormation.attempt {
      val size = formation.slots(FormationContext(count, 0.0, setting)).size
      // Every phase of the cycle, not only the one at rest: a moving shape can fail half way round.
      CustomSlots.travel(layout)
      (size, layout(0.0))
    }
    outcome match
      case Left(reason) => Result(request.id, List(Problem(0, 0, reason)), None)
      case Right((size, _)) if size != count =>
        val message = s"the formation laid out $size slots for $count robots: build it from ctx.count"
        Result(request.id, List(Problem(0, 0, message)), None)
      case Right((_, preview)) => Result(request.id, Nil, preview)

  def encode(result: Result): ujson.Value =
    ujson.Obj(
      "id" -> result.id,
      "ok" -> result.ok,
      "problems" -> ujson.Arr.from(result.problems.map { problem =>
        ujson.Obj("line" -> problem.line, "column" -> problem.column, "message" -> problem.message)
      }),
      "preview" -> result.preview.fold[ujson.Value](ujson.Null) { slots =>
        ujson.Arr.from(slots.map((x, y) => ujson.Arr(x, y)))
      }
    )
