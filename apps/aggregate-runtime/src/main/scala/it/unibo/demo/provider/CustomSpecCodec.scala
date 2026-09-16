package it.unibo.demo.provider

import it.unibo.demo.scenarios.{CustomSlots, CustomSpec, Formula}
import scala.util.Try

/**
 * Reads the optional `custom` object of a `/config/formation` payload into a compiled
 * [[CustomSpec]], on the MQTT callback thread, once per message -- so the 5 Hz control loop only
 * ever evaluates formulas and never parses them.
 *
 * Hand-walked rather than derived from a case class, for the reasons [[MqttProvider]] already
 * demonstrates: it uses `macroRW` for the strict, machine-generated payloads and walks `ujson` by
 * hand for `/config/formation`. That line matters more here. `/config/formation` is retained and
 * now agent-authored, so it must be lenient: an upickle `read` throws on any deviation -- one
 * unexpected key, a point with three numbers -- and that failure would propagate out of the
 * handler's for-comprehension and take the `program`, `leaderId` and `anchor` update down with
 * it. On a retained topic that is not a rejected shape but a stuck fleet, with no live publisher
 * to correct it. Per-field leniency gives graceful degradation instead.
 *
 * Total by construction: a hostile payload can only ever produce [[CustomSpec.Invalid]].
 */
object CustomSpecCodec:

  /** The payload key. A sibling of `params`, not an entry in it: `params` stays numbers-only. */
  val Key: String = "custom"

  /**
   * The spec this message asks for, or `None` when it says nothing about one.
   *
   * `None` must leave the previous spec in place -- the same "a message that omits a key changes
   * nothing about that key" semantics `params` already has, and what an operator expects when
   * they switch to `circleShape` and back. An explicit null clears it to [[CustomSpec.Absent]].
   *
   * Deliberately unlike `leaderId`, which a message without the key does clear: that is
   * documented in [[MqttProvider]] and is about never rooting a gradient on a stale device id.
   */
  def fromCommand(command: ujson.Value): Option[CustomSpec] =
    Try(command.obj.get(Key)).toOption.flatten.map(decode)

  /** Never throws; a payload it cannot use becomes [[CustomSpec.Invalid]] with a reason. */
  def decode(node: ujson.Value): CustomSpec =
    Try(read(node)).getOrElse(CustomSpec.Invalid("custom could not be read"))

  private def read(node: ujson.Value): CustomSpec =
    if node.isNull then CustomSpec.Absent
    else
      node.objOpt match
        case None => CustomSpec.Invalid("custom must be a JSON object")
        case Some(fields) =>
          val declared = fields.get("kind").flatMap(_.strOpt)
          val inferred = declared.orElse {
            if fields.contains("points") then Some("points")
            else if fields.contains("x") && fields.contains("y") then Some("cartesian")
            else if fields.contains("r") && fields.contains("theta") then Some("polar")
            else None
          }
          inferred match
            case Some("points") => points(fields)
            case Some("cartesian") => formulas(fields, "x", "y", CustomSpec.Cartesian.apply)
            case Some("polar") => formulas(fields, "r", "theta", CustomSpec.Polar.apply)
            case Some(other) =>
              CustomSpec.Invalid(s"custom.kind '$other' is not one of points, cartesian, polar")
            case None =>
              CustomSpec.Invalid("custom must carry points, or x and y, or r and theta")

  private def points(fields: scala.collection.Map[String, ujson.Value]): CustomSpec =
    fields.get("points").flatMap(_.arrOpt) match
      case None => CustomSpec.Invalid("custom.points must be an array of [x, y] pairs")
      case Some(entries) =>
        // A malformed or non-finite pair is dropped and the rest survive. A JSON literal too
        // large for a double parses to an infinity, which is the only route by which JSON can
        // smuggle a non-finite number in at all.
        val usable = entries.iterator
          .flatMap { entry =>
            entry.arrOpt
              .filter(_.size >= 2)
              .flatMap { pair =>
                for
                  x <- pair(0).numOpt if x.isFinite
                  y <- pair(1).numOpt if y.isFinite
                yield (x, y)
              }
          }
          .take(CustomSpec.MaxPoints)
          .toList
        if usable.isEmpty then CustomSpec.Invalid("custom.points carried no usable [x, y] pair")
        else
          val declaredClosed = fields.get("closed").flatMap(_.boolOpt).getOrElse(false)
          // An author who repeated the first point at the end meant a closed loop; keeping the
          // duplicate would put two robots on the seam.
          val repeatsFirst = usable.size > 2
            && math.hypot(usable.head._1 - usable.last._1, usable.head._2 - usable.last._2) < CustomSlots.Epsilon
          if repeatsFirst then CustomSpec.Points(usable.init, closed = true)
          else CustomSpec.Points(usable, declaredClosed)

  private def formulas(
      fields: scala.collection.Map[String, ujson.Value],
      firstKey: String,
      secondKey: String,
      build: (Formula, Formula) => CustomSpec
  ): CustomSpec =
    (fields.get(firstKey).flatMap(_.strOpt), fields.get(secondKey).flatMap(_.strOpt)) match
      case (Some(firstSource), Some(secondSource)) =>
        // Naming the offending field is the whole point: the reason travels back to the agent
        // that wrote the formula, which can then fix that one expression.
        (Formula.compile(firstSource), Formula.compile(secondSource)) match
          case (Right(first), Right(second)) => build(first, second)
          case (Left(reason), _) => CustomSpec.Invalid(s"custom.$firstKey: $reason")
          case (_, Left(reason)) => CustomSpec.Invalid(s"custom.$secondKey: $reason")
      case _ =>
        CustomSpec.Invalid(s"custom.$firstKey and custom.$secondKey must both be formula strings")
