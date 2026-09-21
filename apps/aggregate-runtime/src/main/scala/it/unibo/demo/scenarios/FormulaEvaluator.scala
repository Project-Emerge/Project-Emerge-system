package it.unibo.demo.scenarios

private[scenarios] object FormulaEvaluator:
  import Formula.*

  def evaluate(formula: Formula, i: Double, n: Double, t: Double): Double =
    def eval(node: Formula): Double = node match
      case Literal(value) => value
      case Var(VarName.I) => i
      case Var(VarName.N) => n
      case Var(VarName.T) => t
      case Neg(arg) => -eval(arg)
      case Add(left, right) => eval(left) + eval(right)
      case Sub(left, right) => eval(left) - eval(right)
      case Mul(left, right) => eval(left) * eval(right)
      case Div(left, right) => eval(left) / eval(right)
      case Mod(left, right) => eval(left) % eval(right)
      case Pow(base, exponent) => math.pow(eval(base), eval(exponent))
      case Call(fn, args) => applyFunction(fn, args.map(eval))
    eval(formula)

  private def applyFunction(fn: Fn, args: List[Double]): Double = fn match
    case Fn.Sin => math.sin(args.head)
    case Fn.Cos => math.cos(args.head)
    case Fn.Tan => math.tan(args.head)
    case Fn.Asin => math.asin(args.head)
    case Fn.Acos => math.acos(args.head)
    case Fn.Atan => math.atan(args.head)
    case Fn.Atan2 => math.atan2(args.head, args(1))
    case Fn.Sqrt => math.sqrt(args.head)
    case Fn.Abs => math.abs(args.head)
    case Fn.Floor => math.floor(args.head)
    case Fn.Ceil => math.ceil(args.head)
    // rint preserves Double range and rounds ties to even.
    case Fn.Round => math.rint(args.head)
    case Fn.Exp => math.exp(args.head)
    case Fn.Log => math.log(args.head)
    case Fn.Sign => math.signum(args.head)
    case Fn.Hypot => math.hypot(args.head, args(1))
    case Fn.Min => args.reduce(math.min)
    case Fn.Max => args.reduce(math.max)
