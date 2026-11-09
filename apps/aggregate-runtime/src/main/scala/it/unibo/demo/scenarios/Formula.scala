package it.unibo.demo.scenarios

import java.util.concurrent.ConcurrentHashMap

/** Arithmetic expressions accepted by custom formations. */
enum Formula:
  case Literal(value: Double)
  case Var(name: Formula.VarName)
  case Neg(arg: Formula)
  case Add(left: Formula, right: Formula)
  case Sub(left: Formula, right: Formula)
  case Mul(left: Formula, right: Formula)
  case Div(left: Formula, right: Formula)
  case Mod(left: Formula, right: Formula)
  case Pow(base: Formula, exponent: Formula)
  case Call(fn: Formula.Fn, args: List[Formula])

object Formula:

  /** Slot index, slot count and wrapped shared phase. */
  enum VarName:
    case I, N, T

  enum Fn(val name: String, val minArity: Int, val maxArity: Int):
    case Sin extends Fn("sin", 1, 1)
    case Cos extends Fn("cos", 1, 1)
    case Tan extends Fn("tan", 1, 1)
    case Asin extends Fn("asin", 1, 1)
    case Acos extends Fn("acos", 1, 1)
    case Atan extends Fn("atan", 1, 1)
    case Atan2 extends Fn("atan2", 2, 2)
    case Sqrt extends Fn("sqrt", 1, 1)
    case Abs extends Fn("abs", 1, 1)
    case Floor extends Fn("floor", 1, 1)
    case Ceil extends Fn("ceil", 1, 1)
    case Round extends Fn("round", 1, 1)
    case Exp extends Fn("exp", 1, 1)
    case Log extends Fn("log", 1, 1)
    case Sign extends Fn("sign", 1, 1)
    case Hypot extends Fn("hypot", 2, 2)
    case Min extends Fn("min", 2, FormulaLimits.MaxArgs)
    case Max extends Fn("max", 2, FormulaLimits.MaxArgs)

  object Fn:
    val byName: Map[String, Fn] = Fn.values.map(fn => fn.name -> fn).toMap

  val Constants: Map[String, Double] = Map("pi" -> math.Pi, "e" -> math.E)

  def parse(source: String): Either[String, Formula] = FormulaParser.parse(source)

  private val memo = ConcurrentHashMap[String, Either[String, Formula]]()

  /** Caches both successful parses and failures; clears when full. */
  def compile(source: String): Either[String, Formula] =
    memo.get(source) match
      case null =>
        val result = parse(source)
        if memo.size >= FormulaLimits.MaxCachedFormulas then memo.clear()
        memo.put(source, result)
        result
      case cached => cached

  private[scenarios] def cacheSize: Int = memo.size

  /** Parsed formulas evaluate to a Double, possibly NaN or infinity for domain errors. */
  def evaluate(formula: Formula, i: Double, n: Double, t: Double): Double =
    FormulaEvaluator.evaluate(formula, i, n, t)

  /** Wraps the shared phase into one turn; non-finite values become zero. */
  def wrapPhase(phase: Double): Double =
    val turn = 2 * math.Pi
    if !phase.isFinite then 0.0
    else phase % turn match
      case wrapped if wrapped < 0 => wrapped + turn
      case wrapped => wrapped
