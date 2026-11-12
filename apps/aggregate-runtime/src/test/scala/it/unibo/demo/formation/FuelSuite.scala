package it.unibo.demo.formation

import it.unibo.demo.formation.Fuel.{Budget, OutOfFuel}

class FuelSuite extends munit.FunSuite:

  private val plentyOfBytes: Long = 1L << 40

  test("work within its budget runs to completion") {
    assertEquals(Fuel.metered(Budget(steps = 10, bytes = plentyOfBytes)) { (1 to 10).foreach(_ => Fuel.tick()); 42 }, Right(42))
  }

  test("running out of steps stops the work") {
    val outcome = Fuel.metered(Budget(steps = 10, bytes = plentyOfBytes))((1 to 11).foreach(_ => Fuel.tick()))
    assert(outcome.left.exists(_.getMessage.contains("steps")), outcome)
  }

  test("charging for many units at once counts them all") {
    val outcome = Fuel.metered(Budget(steps = 10, bytes = plentyOfBytes))(Fuel.charge(11))
    assert(outcome.isLeft, outcome)
  }

  test("running out is final: work that swallows it still fails") {
    val outcome = Fuel.metered(Budget(steps = 1, bytes = plentyOfBytes)) {
      try
        Fuel.tick()
        Fuel.tick()
      catch case _: OutOfFuel => ()
      7
    }
    assert(outcome.isLeft, outcome)
  }

  test("allocating past the memory budget stops the work") {
    assume(Fuel.measuresAllocation, "this JVM does not report per-thread allocation")
    val outcome = Fuel.metered(Budget(steps = Long.MaxValue, bytes = 1L << 20)) {
      (1 to 100).foldLeft(List.empty[Array[Byte]]) { (kept, _) =>
        Fuel.tick()
        Array.ofDim[Byte](100_000) :: kept
      }.size
    }
    assert(outcome.left.exists(_.getMessage.contains("bytes")), outcome)
  }

  test("metered code is refused outside any meter") {
    intercept[OutOfFuel](Fuel.tick())
  }

  test("an inner meter neither spends nor refills the outer one") {
    val outcome = Fuel.metered(Budget(steps = 2, bytes = plentyOfBytes)) {
      val inner = Fuel.metered(Budget(steps = 1, bytes = plentyOfBytes)) { Fuel.tick(); Fuel.tick() }
      Fuel.tick()
      Fuel.tick()
      inner.isLeft
    }
    assertEquals(outcome, Right(true))
  }

  test("recursing past the stack is reported as running out, not thrown") {
    def deep(k: Long): Long = if k == 0 then 0 else 1 + deep(k - 1)
    val outcome = Fuel.metered(Budget.Default)(deep(Long.MaxValue))
    assert(outcome.left.exists(_.getMessage.contains("deep")), outcome)
  }
