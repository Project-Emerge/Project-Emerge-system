package it.unibo.demo.serialization

import play.api.libs.json.{JsValue, JsObject}

/** Trait for pluggable serialization of custom domain types that ScaFi EXPORTs might hold.
  * This allows decoupling the general ExportJsonCodec serialization framework from specific
  * application domain types like Actuation.
  */
trait CustomValueCodec:
  /** Attempt to serialize a custom type.
    * Returns Some(JsObject) containing "t" (tag) and optional "v" (value) if the type is supported,
    * or None otherwise.
    */
  def serialize(v: Any, recursiveWrite: Any => JsValue): Option[JsObject]

  /** Attempt to deserialize a custom tag.
    * Returns Some(Any) if the tag is supported, or None otherwise.
    */
  def deserialize(tag: String, value: JsValue, recursiveRead: JsValue => Any): Option[Any]

/** Registry for custom codecs. Modules can register their custom codecs here on startup.
  */
object CustomValueRegistry:
  private val codecs = java.util.concurrent.CopyOnWriteArrayList[CustomValueCodec]()

  def register(codec: CustomValueCodec): Unit = codecs.add(codec)
  def unregister(codec: CustomValueCodec): Unit = codecs.remove(codec)

  def serialize(v: Any, recursiveWrite: Any => JsValue): Option[JsObject] =
    val it = codecs.iterator()
    var result: Option[JsObject] = None
    while (it.hasNext && result.isEmpty) {
      result = it.next().serialize(v, recursiveWrite)
    }
    result

  def deserialize(tag: String, value: JsValue, recursiveRead: JsValue => Any): Option[Any] =
    val it = codecs.iterator()
    var result: Option[Any] = None
    while (it.hasNext && result.isEmpty) {
      result = it.next().deserialize(tag, value, recursiveRead)
    }
    result
