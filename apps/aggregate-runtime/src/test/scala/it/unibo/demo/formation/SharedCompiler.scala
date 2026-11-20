package it.unibo.demo.formation

/** One warm compiler for every suite in the forked test JVM: a cold one costs seconds. */
object SharedCompiler:
  lazy val instance: FormationCompiler = FormationCompiler()
