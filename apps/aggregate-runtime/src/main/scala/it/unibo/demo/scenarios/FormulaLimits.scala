package it.unibo.demo.scenarios

/** Kept independent of Formula to avoid enum initialisation cycles. */
object FormulaLimits:
  val MaxSourceLength: Int = 512
  val MaxNodes: Int = 256

  // Parentheses consume depth without adding nodes.
  val MaxDepth: Int = 32
  val MaxArgs: Int = 8
  val MaxCachedFormulas: Int = 64
