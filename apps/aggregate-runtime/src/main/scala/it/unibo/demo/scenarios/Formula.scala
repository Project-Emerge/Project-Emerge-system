package it.unibo.demo.scenarios

import scala.util.control.NoStackTrace

/**
 * Bounds on what a published formula may be.
 *
 * Its own object rather than fields of [[Formula]] so that [[Formula.Fn]]'s parameterised cases
 * can name `MaxArgs` without depending on the initialisation order of the `Formula` companion.
 */
object FormulaLimits:
  /** Characters. Rejected before a single token is read. */
  val MaxSourceLength: Int = 512

  /** AST nodes. Counted as the parser builds, so the budget is spent rather than measured after. */
  val MaxNodes: Int = 256

  /**
   * Grammar nesting depth. Mandatory and separate from [[MaxNodes]]: a parenthesis costs a stack
   * frame but builds no node, so a source of 100000 open parentheses would overflow the stack
   * long before the node budget noticed.
   */
  val MaxDepth: Int = 32

  /** Arguments to one call. */
  val MaxArgs: Int = 8

  /** Distinct sources kept in the parse memo. The live working set is two or three. */
  val MaxCachedFormulas: Int = 64

/**
 * A parsed arithmetic formula: the whole of what a published `custom` spec may say about where a
 * slot goes.
 *
 * An interpreted tree and nothing else -- no reflection, no Scala compiler, no `eval`, no name
 * that is not in [[Formula.Fn]], [[Formula.VarName]] or [[Formula.Constants]]. Once parsed,
 * evaluation is a fold over primitive `Double` operations, so it can neither throw nor block:
 * every domain error IEEE-754 admits (a square root of a negative, a log of zero, zero over
 * zero, a modulo by zero) yields a NaN or an infinity, which [[CustomSlots]] then coerces. Work
 * is bounded by [[FormulaLimits.MaxNodes]] and recursion by [[FormulaLimits.MaxDepth]], so no
 * payload can stall the 5 Hz control loop.
 */
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

  /** The variables in scope. Resolved at parse time, so evaluation does no map lookup. */
  enum VarName:
    /** 0-based slot index, from zero up to but excluding the slot count. */
    case I

    /** Number of slots, i.e. `ctx.count`. */
    case N

    /**
     * Shared phase in radians, wrapped into one turn by [[wrapPhase]]. One full turn per
     * `wavePeriod` seconds.
     *
     * Wrapped deliberately: `ShapeFormation.phase` is measured from the epoch and is currently
     * about 1.9e9 radians. That is harmless inside a sine, which is all the compiled-in shapes do
     * with it, but nonsense as a coordinate -- half of it, or its floor, in a published formula
     * would fling the fleet into the next county.
     */
    case T

  /** The function whitelist. Nothing outside it can be named, so nothing else is reachable. */
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
    // Variadic, because an author writing a three-way minimum should not have to nest calls.
    case Min extends Fn("min", 2, FormulaLimits.MaxArgs)
    case Max extends Fn("max", 2, FormulaLimits.MaxArgs)

  object Fn:
    val byName: Map[String, Fn] = Fn.values.map(fn => fn.name -> fn).toMap

  val Constants: Map[String, Double] = Map("pi" -> math.Pi, "e" -> math.E)

  private val variables: Map[String, VarName] =
    Map("i" -> VarName.I, "n" -> VarName.N, "t" -> VarName.T)

  /** Signalled internally and caught at the [[parse]] boundary. `NoStackTrace` keeps it cheap. */
  private final class ParseError(val reason: String) extends RuntimeException(reason), NoStackTrace

  /**
   * Recursive-descent parser.
   *
   * Precedence, low to high: addition, then multiplication, then unary sign, then exponentiation
   * (right-associative), then atoms. So a negated square is negative and a tower of exponents
   * folds from the right, which are the conventions a formula's author will expect.
   */
  private final class Parser(source: String):
    private var position: Int = 0
    private var nodes: Int = 0
    private var depth: Int = 0

    def parseAll(): Formula =
      val result = expr()
      skipWhitespace()
      if position < source.length then fail(s"unexpected input at offset $position")
      result

    private def fail(reason: String): Nothing = throw ParseError(reason)

    private def spend(): Unit =
      nodes += 1
      if nodes > FormulaLimits.MaxNodes then
        fail(s"formula has more than ${FormulaLimits.MaxNodes} operations")

    private def nested[A](body: => A): A =
      depth += 1
      if depth > FormulaLimits.MaxDepth then
        fail(s"formula is nested deeper than ${FormulaLimits.MaxDepth} levels")
      try body
      finally depth -= 1

    private def skipWhitespace(): Unit =
      while position < source.length && source(position).isWhitespace do position += 1

    private def peek(): Char =
      skipWhitespace()
      if position < source.length then source(position) else ' '

    private def eat(expected: Char): Boolean =
      if peek() == expected then
        position += 1
        true
      else false

    private def expr(): Formula = nested {
      var left = term()
      var continue = true
      while continue do
        peek() match
          case '+' =>
            position += 1
            spend()
            left = Formula.Add(left, term())
          case '-' =>
            position += 1
            spend()
            left = Formula.Sub(left, term())
          case _ => continue = false
      left
    }

    private def term(): Formula = nested {
      var left = unary()
      var continue = true
      while continue do
        peek() match
          case '*' =>
            position += 1
            spend()
            left = Formula.Mul(left, unary())
          case '/' =>
            position += 1
            spend()
            left = Formula.Div(left, unary())
          case '%' =>
            position += 1
            spend()
            left = Formula.Mod(left, unary())
          case _ => continue = false
      left
    }

    private def unary(): Formula = nested {
      peek() match
        case '-' =>
          position += 1
          spend()
          Formula.Neg(unary())
        case '+' =>
          position += 1
          unary()
        case _ => power()
    }

    // The exponent is a `unary`, so a negative exponent needs no parentheses, and recursing on
    // `unary` rather than `power` is what makes exponentiation right-associative.
    private def power(): Formula = nested {
      val base = atom()
      if eat('^') then
        spend()
        Formula.Pow(base, unary())
      else base
    }

    private def atom(): Formula = nested {
      val ch = peek()
      if ch == '(' then
        position += 1
        val inner = expr()
        if !eat(')') then fail(s"missing a closing parenthesis at offset $position")
        inner
      else if ch.isDigit || ch == '.' then number()
      else if ch.isLetter then identifier()
      else if position >= source.length then fail("formula ends where a value was expected")
      else fail(s"unexpected character '$ch' at offset $position")
    }

    private def number(): Formula =
      val start = position
      while position < source.length && source(position).isDigit do position += 1
      if position < source.length && source(position) == '.' then
        position += 1
        while position < source.length && source(position).isDigit do position += 1
      // The letter e is both a constant and an exponent marker. It is only consumed as an
      // exponent while a number token is already in progress AND a digit follows the optional
      // sign, so a mantissa with an exponent is one number, a bare e is Euler's constant, and a
      // digit followed by a space and an e stays a parse error rather than silently becoming a
      // power of ten.
      if position < source.length && (source(position) == 'e' || source(position) == 'E') then
        val marker = position
        var lookahead = position + 1
        if lookahead < source.length && (source(lookahead) == '+' || source(lookahead) == '-') then
          lookahead += 1
        if lookahead < source.length && source(lookahead).isDigit then
          position = lookahead
          while position < source.length && source(position).isDigit do position += 1
        else position = marker
      val text = source.substring(start, position)
      spend()
      Formula.Literal(text.toDouble)

    private def identifier(): Formula =
      val start = position
      while position < source.length
        && (source(position).isLetterOrDigit || source(position) == '_')
      do position += 1
      val name = source.substring(start, position)
      if peek() == '(' then
        position += 1
        val fn = Fn.byName.getOrElse(name, fail(s"unknown function '$name'"))
        val args = if peek() == ')' then List.empty else argumentList()
        if !eat(')') then fail(s"missing a closing parenthesis after '$name'")
        if args.size < fn.minArity || args.size > fn.maxArity then
          val expected =
            if fn.minArity == fn.maxArity then s"${fn.minArity}"
            else s"${fn.minArity} to ${fn.maxArity}"
          fail(s"'$name' takes $expected arguments but was given ${args.size}")
        spend()
        Formula.Call(fn, args)
      else
        variables.get(name) match
          case Some(variable) =>
            spend()
            Formula.Var(variable)
          case None =>
            Constants.get(name) match
              case Some(value) =>
                spend()
                Formula.Literal(value)
              case None => fail(s"unknown name '$name'")

    private def argumentList(): List[Formula] =
      val builder = List.newBuilder[Formula]
      builder += expr()
      var count = 1
      while eat(',') do
        count += 1
        if count > FormulaLimits.MaxArgs then
          fail(s"a call takes at most ${FormulaLimits.MaxArgs} arguments")
        builder += expr()
      builder.result()

  /** Parses `source`, or says why it cannot describe a formation. */
  def parse(source: String): Either[String, Formula] =
    if source == null || source.isEmpty then Left("formula is empty")
    else if source.length > FormulaLimits.MaxSourceLength then
      Left(s"formula is longer than ${FormulaLimits.MaxSourceLength} characters")
    else
      try Right(Parser(source).parseAll())
      catch
        case error: ParseError => Left(error.reason)
        // Anything unforeseen -- a numeric literal the lexer accepted but the JDK will not parse,
        // say. A rejection is always preferable to letting an exception travel towards a control
        // loop, so this evaluator's totality does not rest on the parser being flawless.
        case error: RuntimeException =>
          Left(s"formula could not be read: ${error.getMessage}")

  private val memo = java.util.concurrent.ConcurrentHashMap[String, Either[String, Formula]]()

  /**
   * [[parse]], memoised on the source text.
   *
   * The memo lives here and not in a `rep` cell. Parsing is a pure function of a string, so a
   * memo shared across devices cannot leak one device's state into another's; `rep` state is lost
   * whenever a robot misses a round, which would make the cost recur unpredictably; and a `rep`
   * cell holding an AST would put a non-value into the export for nothing.
   *
   * In practice this is never consulted at 5 Hz, because
   * [[it.unibo.demo.provider.CustomSpecCodec]] compiles a formula once, on the MQTT callback
   * thread, as the message arrives. This is the backstop for the other producers -- tests and
   * defaults -- and is bounded by [[FormulaLimits.MaxCachedFormulas]]: over the cap it is cleared
   * wholesale, which is ample for a working set of two or three formulas.
   */
  def compile(source: String): Either[String, Formula] =
    val cached = memo.get(source)
    if cached != null then cached
    else
      val result = parse(source)
      // Failures are cached too, so a hostile source is only ever parsed once.
      if memo.size >= FormulaLimits.MaxCachedFormulas then memo.clear()
      memo.put(source, result)
      result

  /** Number of memoised sources. For the bounded-cache test only. */
  private[scenarios] def cacheSize: Int = memo.size

  /**
   * Evaluates a formula. Total: it returns a `Double` for every input, possibly non-finite, and
   * never throws.
   *
   * `i`, `n` and `t` are passed as bare `Double`s rather than wrapped in a case class so that a
   * round allocates nothing per slot.
   */
  def evaluate(formula: Formula, i: Double, n: Double, t: Double): Double = formula match
    case Formula.Literal(value) => value
    case Formula.Var(VarName.I) => i
    case Formula.Var(VarName.N) => n
    case Formula.Var(VarName.T) => t
    case Formula.Neg(arg) => -evaluate(arg, i, n, t)
    case Formula.Add(left, right) => evaluate(left, i, n, t) + evaluate(right, i, n, t)
    case Formula.Sub(left, right) => evaluate(left, i, n, t) - evaluate(right, i, n, t)
    case Formula.Mul(left, right) => evaluate(left, i, n, t) * evaluate(right, i, n, t)
    case Formula.Div(left, right) => evaluate(left, i, n, t) / evaluate(right, i, n, t)
    // Java's remainder on doubles yields NaN for a zero divisor rather than throwing, which is
    // exactly the totality this evaluator promises.
    case Formula.Mod(left, right) => evaluate(left, i, n, t) % evaluate(right, i, n, t)
    case Formula.Pow(base, exponent) =>
      math.pow(evaluate(base, i, n, t), evaluate(exponent, i, n, t))
    case Formula.Call(fn, args) => call(fn, args, i, n, t)

  private def call(fn: Fn, args: List[Formula], i: Double, n: Double, t: Double): Double =
    def first: Double = evaluate(args.head, i, n, t)
    def second: Double = evaluate(args(1), i, n, t)
    fn match
      case Fn.Sin => math.sin(first)
      case Fn.Cos => math.cos(first)
      case Fn.Tan => math.tan(first)
      case Fn.Asin => math.asin(first)
      case Fn.Acos => math.acos(first)
      case Fn.Atan => math.atan(first)
      case Fn.Atan2 => math.atan2(first, second)
      case Fn.Sqrt => math.sqrt(first)
      case Fn.Abs => math.abs(first)
      case Fn.Floor => math.floor(first)
      case Fn.Ceil => math.ceil(first)
      // `rint`, not `round`: it returns a `Double` and does not saturate at `Long.MaxValue`,
      // which matters because the argument may be astronomically large.
      case Fn.Round => math.rint(first)
      case Fn.Exp => math.exp(first)
      case Fn.Log => math.log(first)
      case Fn.Sign => math.signum(first)
      case Fn.Hypot => math.hypot(first, second)
      case Fn.Min => args.iterator.map(evaluate(_, i, n, t)).reduce(math.min)
      case Fn.Max => args.iterator.map(evaluate(_, i, n, t)).reduce(math.max)

  /** Wraps a reading of the shared clock into one turn; a non-finite phase becomes zero. */
  def wrapPhase(phase: Double): Double =
    if !phase.isFinite then 0.0
    else
      val turn = 2 * math.Pi
      val wrapped = phase % turn
      if wrapped < 0 then wrapped + turn else wrapped
