package it.unibo.demo.formation

import java.lang.management.ManagementFactory

/**
 * How much work compiled snippet code may do, per evaluation, counted where it happens.
 *
 * [[SandboxPhase]] puts a [[tick]] at the top of every method and lambda an author writes, and
 * in every by-name argument, so any loop -- always a recursion, since `while` is refused --
 * pays one step per turn. Builders that turn a number into that many elements, the DSL's and
 * `until`, [[charge]] for all of them up front. At every step the thread's allocation is
 * checked too, so a snippet that doubles its data each call stops long before the heap does.
 *
 * Exhaustion surfaces as [[OutOfFuel]] thrown into the snippet, since nothing else can unwind
 * code that never returns, and comes out of [[metered]] as a `Left`. It is final: a snippet
 * cannot catch it and carry on, because every later step throws again.
 *
 * What it cannot bound: a single library call, which costs one step whatever it does inside.
 * That is why [[SandboxPolicy]] refuses the calls that build something large from a number alone.
 */
object Fuel:

  /**
   * @param steps metered calls, plus one per element a builder makes
   * @param bytes heap the evaluating thread may allocate, garbage included
   */
  final case class Budget(steps: Long, bytes: Long)

  object Budget:
    /** Ample for laying out a fleet of dozens, and a few milliseconds of work at most. */
    val Default: Budget = Budget(steps = 100_000L, bytes = 32L * 1024 * 1024)

  /** Thrown by design and possibly often, so it carries no stack trace. */
  final class OutOfFuel(message: String) extends RuntimeException(message, null, false, false)

  private val allocation: Option[com.sun.management.ThreadMXBean] =
    ManagementFactory.getThreadMXBean match
      case bean: com.sun.management.ThreadMXBean
          if bean.isThreadAllocatedMemorySupported && bean.isThreadAllocatedMemoryEnabled =>
        Some(bean)
      case _ => None

  /** Whether the memory half of a budget is enforced; HotSpot JVMs report it. */
  val measuresAllocation: Boolean = allocation.isDefined

  /** Runs `body` with `budget` to spend; a meter already running is suspended, not drawn on. */
  def metered[A](budget: Budget)(body: => A): Either[OutOfFuel, A] =
    val meter = meters.get()
    val outer = meter.snapshot
    meter.start(budget.steps, allocatedBytes() + budget.bytes)
    try
      val result = body
      // A snippet that caught its own exhaustion still failed.
      meter.exhaustion.toLeft(result)
    catch
      case outOfFuel: OutOfFuel => Left(outOfFuel)
      case _: StackOverflowError => Left(OutOfFuel("the formation recursed too deep"))
    finally meter.restore(outer)

  /** One step. Inserted by the compiler; snippets cannot call it themselves. */
  def tick(): Unit = charge(1)

  /** `units` steps at once, for a builder about to make that many elements. */
  def charge(units: Long): Unit =
    val meter = meters.get()
    if !meter.active then throw OutOfFuel("formation code ran outside a fuel meter")
    meter.steps -= units
    if meter.steps < 0 then throw meter.exhaust("the formation ran out of steps")
    if allocatedBytes() > meter.bytesLimit then
      throw meter.exhaust("the formation allocated more bytes than its budget")

  private final case class MeterState(
      active: Boolean,
      steps: Long,
      bytesLimit: Long,
      exhaustion: Option[OutOfFuel]
  )

  /** Mutable because [[charge]] runs on every metered call; confined to one thread. */
  private final class Meter:
    var active: Boolean = false
    var steps: Long = 0L
    var bytesLimit: Long = Long.MaxValue
    var exhaustion: Option[OutOfFuel] = None

    def snapshot: MeterState = MeterState(active, steps, bytesLimit, exhaustion)

    def start(stepBudget: Long, limit: Long): Unit = restore(MeterState(true, stepBudget, limit, None))

    def restore(state: MeterState): Unit =
      active = state.active
      steps = state.steps
      bytesLimit = state.bytesLimit
      exhaustion = state.exhaustion

    /** Pins the step count below zero, so that every later step throws again. */
    def exhaust(reason: String): OutOfFuel =
      steps = Long.MinValue / 2
      val outOfFuel = exhaustion.getOrElse(OutOfFuel(reason))
      exhaustion = Some(outOfFuel)
      outOfFuel

  private val meters: ThreadLocal[Meter] = ThreadLocal.withInitial(() => Meter())

  private def allocatedBytes(): Long = allocation.fold(0L)(_.getCurrentThreadAllocatedBytes)
