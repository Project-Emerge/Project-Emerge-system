package it.unibo.core.aggregate

import it.unibo.scafi.incarnations.BasicAbstractIncarnation
import it.unibo.scafi.lib.{StandardLibrary, StdLibExplicitFields}

/**
 * Base ScaFi incarnation for this service.
 * An incarnation encodes:
 * - the types family used in the program (e.g., the ID type, the position type, etc.)
 *    - in this case, ID = Int
 * - the aggregate context used in the program (e.g., the aggregate execution strategy, the aggregate export strategy, etc.)
 *
 * to define a program, you need to import the incarnation and extend the `AggregateProgram` trait.
 *
 * import it.unibo.core.incarnations.AggregateIncarnation.*
 * class MyAggregateProgram extends AggregateProgram:
 *  def main() = foldhood(0)(_+_)(1)
 *
 *
 */
object AggregateIncarnation extends BasicAbstractIncarnation with BuildingBlocks
