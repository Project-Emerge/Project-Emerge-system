package it.unibo.demo.serialization

import it.unibo.core.aggregate.AggregateIncarnation.*
import play.api.libs.json.*

import scala.util.{Failure, Success, Try}

/** JSON (de)serialization of scafi EXPORTs so runtimes can exchange them over MQTT.
  *
  * It mirrors the scheme used by `scafi-distributed`'s `AbstractJsonIncarnationSerializer`
  * (ComputationExport → list of Path→value, Path → list of Slot, tagged Slot/value objects) but uses
  * play-json and stays decoupled from Akka.
  *
  * It uses idiomatic play-json [[Format]] typeclasses to modularize the serialization logic.
  * It supports custom/extensible value types (like Actuation) via a pluggable [[CustomValueRegistry]].
  */
object ExportJsonCodec:

  // ---- StackTraceElement (FunCall.funId) Format ----
  given steFormat: Format[StackTraceElement] with
    override def writes(e: StackTraceElement): JsValue = Json.obj(
      "cl" -> Option(e.getClassLoaderName),
      "mod" -> Option(e.getModuleName),
      "mv" -> Option(e.getModuleVersion),
      "dc" -> e.getClassName,
      "mn" -> e.getMethodName,
      "fn" -> Option(e.getFileName),
      "ln" -> e.getLineNumber
    )
    override def reads(j: JsValue): JsResult[StackTraceElement] = Try:
      new StackTraceElement(
        (j \ "cl").asOpt[String].orNull,
        (j \ "mod").asOpt[String].orNull,
        (j \ "mv").asOpt[String].orNull,
        (j \ "dc").as[String],
        (j \ "mn").as[String],
        (j \ "fn").asOpt[String].orNull,
        (j \ "ln").as[Int]
      )
    match
      case Success(ste) => JsSuccess(ste)
      case Failure(err) => JsError(err.getMessage)

  // ---- Slot Format ----
  given slotFormat(using Format[StackTraceElement]): Format[Slot] with
    override def writes(s: Slot): JsValue = s.getClass.getSimpleName match
      case n if n.startsWith("Nbr")      => Json.obj("s" -> "Nbr", "i" -> s.asInstanceOf[Nbr[Any]].index)
      case n if n.startsWith("Rep")      => Json.obj("s" -> "Rep", "i" -> s.asInstanceOf[Rep[Any]].index)
      case n if n.startsWith("FoldHood") => Json.obj("s" -> "FoldHood", "i" -> s.asInstanceOf[FoldHood[Any]].index)
      case n if n.startsWith("FunCall")  =>
        val f = s.asInstanceOf[FunCall[Any]]
        Json.obj("s" -> "FunCall", "i" -> f.index, "fun" -> Json.toJson(f.funId.asInstanceOf[StackTraceElement]))
      case n if n.startsWith("Scope")    =>
        Json.obj("s" -> "Scope", "k" -> s.asInstanceOf[Scope[Any]].key.asInstanceOf[Class[?]].getName)
      case other => throw IllegalArgumentException(s"Unsupported slot type: $other")

    override def reads(j: JsValue): JsResult[Slot] = Try:
      (j \ "s").as[String] match
        case "Nbr"      => Nbr[Any]((j \ "i").as[Int])
        case "Rep"      => Rep[Any]((j \ "i").as[Int])
        case "FoldHood" => FoldHood[Any]((j \ "i").as[Int])
        case "FunCall"  => FunCall[Any]((j \ "i").as[Int], (j \ "fun").as[StackTraceElement])
        case "Scope"    => Scope[Any](Class.forName((j \ "k").as[String]))
        case other      => throw IllegalArgumentException(s"Unsupported slot tag: $other")
    match
      case Success(slot) => JsSuccess(slot)
      case Failure(err)  => JsError(err.getMessage)

  // ---- Values (Any) Format ----
  given valueFormat: Format[Any] with
    override def writes(v: Any): JsValue = v match
      case null          => Json.obj("t" -> "null")
      // Encoded as text so non-finite doubles round-trip safely.
      case d: Double     => Json.obj("t" -> "d", "v" -> d.toString)
      case i: Int        => Json.obj("t" -> "i", "v" -> i)
      case l: Long       => Json.obj("t" -> "l", "v" -> l)
      case b: Boolean    => Json.obj("t" -> "b", "v" -> b)
      case s: String     => Json.obj("t" -> "s", "v" -> s)
      case (a, b)        => Json.obj("t" -> "t2", "v" -> Json.arr(writes(a), writes(b)))
      case o: Option[?]  => o match
        case Some(x) => Json.obj("t" -> "some", "v" -> writes(x))
        case None    => Json.obj("t" -> "none")
      case m: Map[?, ?] =>
        Json.obj("t" -> "m", "v" -> JsArray(m.toSeq.map((k, vv) => Json.arr(writes(k), writes(vv)))))
      case other =>
        CustomValueRegistry.serialize(other, writes)
          .getOrElse(throw IllegalArgumentException(s"Unsupported export value type: ${other.getClass.getName}"))

    override def reads(j: JsValue): JsResult[Any] = Try:
      val tag = (j \ "t").as[String]
      tag match
        case "null" => null
        case "d"    => (j \ "v").as[String].toDouble
        case "i"    => (j \ "v").as[Int]
        case "l"    => (j \ "v").as[Long]
        case "b"    => (j \ "v").as[Boolean]
        case "s"    => (j \ "v").as[String]
        case "t2"   =>
          val a = (j \ "v").as[JsArray]
          (reads(a(0)).get, reads(a(1)).get)
        case "some" => Some(reads((j \ "v").get).get)
        case "none" => None
        case "m"    => (j \ "v").as[JsArray].value.map(e => reads(e(0)).get -> reads(e(1)).get).toMap
        case other  =>
          CustomValueRegistry.deserialize(other, (j \ "v").getOrElse(JsNull), js => reads(js).get)
            .getOrElse(throw IllegalArgumentException(s"Unsupported export value tag: $other"))
    match
      case Success(value) => JsSuccess(value)
      case Failure(err)   => JsError(err.getMessage)

  // ---- Export ----
  def encode(ex: EXPORT): String =
    val paths = JsArray(ex.paths.toSeq.map { (path, value) =>
      Json.obj(
        "p" -> JsArray(path.path.map(Json.toJson(_))),
        "v" -> Json.toJson(value)
      )
    })
    Json.stringify(Json.obj("paths" -> paths))

  def decode(s: String): EXPORT =
    val pairs = (Json.parse(s) \ "paths").as[JsArray].value.map { e =>
      val slots = (e \ "p").as[JsArray].value.map(_.as[Slot]).toList
      val value = (e \ "v").as[Any]
      (new PathImpl(slots): Path, value)
    }
    factory.createExport(pairs.toSeq*)
