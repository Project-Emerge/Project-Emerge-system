package it.unibo.demo.robot

import it.unibo.demo.serialization.{CustomValueCodec, CustomValueRegistry}
import play.api.libs.json.{JsValue, JsObject, Json}

/** Pluggable serializer/deserializer for domain-specific Actuation values.
  */
object ActuationCodec extends CustomValueCodec:
  def register(): Unit = CustomValueRegistry.register(this)

  override def serialize(v: Any, recursiveWrite: Any => JsValue): Option[JsObject] = v match
    case Actuation.Rotation(vec) => Some(Json.obj("t" -> "rot", "v" -> recursiveWrite(vec)))
    case Actuation.Forward(vec)  => Some(Json.obj("t" -> "fwd", "v" -> recursiveWrite(vec)))
    case Actuation.NoOp          => Some(Json.obj("t" -> "noop"))
    case Actuation.Stop          => Some(Json.obj("t" -> "stop"))
    case _                       => None

  override def deserialize(tag: String, value: JsValue, recursiveRead: JsValue => Any): Option[Any] = tag match
    case "rot"  => Some(Actuation.Rotation(recursiveRead(value).asInstanceOf[(Double, Double)]))
    case "fwd"  => Some(Actuation.Forward(recursiveRead(value).asInstanceOf[(Double, Double)]))
    case "noop" => Some(Actuation.NoOp)
    case "stop" => Some(Actuation.Stop)
    case _      => None
