package it.unibo.demo.scenarios

import scala.annotation.tailrec
import scala.util.control.NoStackTrace

private[scenarios] object FormulaParser:
  import Formula.*

  def parse(source: String): Either[String, Formula] =
    if source == null || source.isEmpty then Left("formula is empty")
    else if source.length > FormulaLimits.MaxSourceLength then
      Left(s"formula is longer than ${FormulaLimits.MaxSourceLength} characters")
    else
      try Right(Parser(source).parse())
      catch
        case error: ParseError => Left(error.reason)
        case error: RuntimeException => Left(s"formula could not be read: ${error.getMessage}")

  private val Variables: Map[String, VarName] =
    Map("i" -> VarName.I, "n" -> VarName.N, "t" -> VarName.T)

  private type BinaryOperator = (Formula, Formula) => Formula

  private val Additive: Map[Char, BinaryOperator] = Map('+' -> Add.apply, '-' -> Sub.apply)

  private val Multiplicative: Map[Char, BinaryOperator] =
    Map('*' -> Mul.apply, '/' -> Div.apply, '%' -> Mod.apply)

  private final class ParseError(val reason: String) extends RuntimeException(reason), NoStackTrace

  private final class Parser(source: String):
    private val End = '\u0000'
    private var position: Int = 0
    private var nodes: Int = 0
    private var depth: Int = 0

    def parse(): Formula =
      val result = expression()
      advanceWhile(_.isWhitespace)
      if position < source.length then fail(s"unexpected input at offset $position")
      result

    private def fail(reason: String): Nothing = throw ParseError(reason)

    private def countNode(): Unit =
      nodes += 1
      if nodes > FormulaLimits.MaxNodes then
        fail(s"formula has more than ${FormulaLimits.MaxNodes} operations")

    private def withinDepthLimit[A](body: => A): A =
      depth += 1
      if depth > FormulaLimits.MaxDepth then
        fail(s"formula is nested deeper than ${FormulaLimits.MaxDepth} levels")
      try body
      finally depth -= 1

    private def current: Char = if position < source.length then source(position) else End

    private def advanceWhile(accepted: Char => Boolean): Unit =
      while position < source.length && accepted(current) do position += 1

    private def peek(): Char =
      advanceWhile(_.isWhitespace)
      current

    private def consume(expected: Char): Boolean =
      val matched = peek() == expected
      if matched then position += 1
      matched

    private def chain(operators: Map[Char, BinaryOperator], operand: () => Formula): Formula = withinDepthLimit:
      @tailrec def fold(left: Formula): Formula = operators.get(peek()) match
        case Some(combine) =>
          position += 1
          countNode()
          fold(combine(left, operand()))
        case None => left
      fold(operand())

    private def expression(): Formula = chain(Additive, term)

    private def term(): Formula = chain(Multiplicative, unary)

    private def unary(): Formula = withinDepthLimit:
      peek() match
        case '-' =>
          position += 1
          countNode()
          Neg(unary())
        case '+' =>
          position += 1
          unary()
        case _ => power()

    // Unary exponents allow 2^-1 and keep exponentiation right-associative.
    private def power(): Formula = withinDepthLimit:
      val base = atom()
      if consume('^') then
        countNode()
        Pow(base, unary())
      else base

    private def atom(): Formula = withinDepthLimit:
      peek() match
        case '(' =>
          position += 1
          val inner = expression()
          if !consume(')') then fail(s"missing a closing parenthesis at offset $position")
          inner
        case End => fail("formula ends where a value was expected")
        case ch if ch.isDigit || ch == '.' => readNumber()
        case ch if ch.isLetter => readIdentifier()
        case ch => fail(s"unexpected character '$ch' at offset $position")

    private def readNumber(): Formula =
      val start = position
      advanceWhile(_.isDigit)
      if current == '.' then
        position += 1
        advanceWhile(_.isDigit)
      readExponent()
      countNode()
      Literal(source.substring(start, position).toDouble)

    private def readExponent(): Unit =
      if current == 'e' || current == 'E' then
        val marker = position
        position += 1
        if current == '+' || current == '-' then position += 1
        if current.isDigit then advanceWhile(_.isDigit) else position = marker

    private def readIdentifier(): Formula =
      val start = position
      advanceWhile(ch => ch.isLetterOrDigit || ch == '_')
      val name = source.substring(start, position)
      val node =
        if consume('(') then readCall(name)
        else
          Variables
            .get(name)
            .map(Var.apply)
            .orElse(Constants.get(name).map(Literal.apply))
            .getOrElse(fail(s"unknown name '$name'"))
      countNode()
      node

    private def readCall(name: String): Formula =
      val fn = Fn.byName.getOrElse(name, fail(s"unknown function '$name'"))
      val args = if peek() == ')' then Nil else readArguments()
      if !consume(')') then fail(s"missing a closing parenthesis after '$name'")
      if args.size < fn.minArity || args.size > fn.maxArity then
        val expected =
          if fn.minArity == fn.maxArity then s"${fn.minArity}"
          else s"${fn.minArity} to ${fn.maxArity}"
        fail(s"'$name' takes $expected arguments but was given ${args.size}")
      Call(fn, args)

    private def readArguments(): List[Formula] =
      @tailrec def collect(acc: List[Formula]): List[Formula] =
        if acc.sizeIs > FormulaLimits.MaxArgs then
          fail(s"a call takes at most ${FormulaLimits.MaxArgs} arguments")
        else if consume(',') then collect(expression() :: acc)
        else acc.reverse
      collect(List(expression()))
