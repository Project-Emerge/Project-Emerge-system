ThisBuild / version := "0.1.0-SNAPSHOT"
ThisBuild / assemblyMergeStrategy := {
  case PathList("javax", "servlet", xs @ _*)         => MergeStrategy.first
  case PathList(ps @ _*) if ps.last endsWith ".html" => MergeStrategy.first
  case "application.conf"                            => MergeStrategy.concat
  case "unwanted.txt"                                => MergeStrategy.discard
  case "module-info.class"                           => MergeStrategy.discard
  case PathList("META-INF", "services", xs @ _*) => MergeStrategy.concat
  case PathList("META-INF", "versions", "9", "module-info.class") => MergeStrategy.discard
  case PathList("META-INF", xs @ _*) => MergeStrategy.discard
  case x =>
    val oldStrategy = (ThisBuild / assemblyMergeStrategy).value
    oldStrategy(x)
}

val underlineJava = System.getProperty("java.version").split("\\.")(0)

ThisBuild / scalaVersion := "3.7.4"
val slf4jVersion = "2.0.17"
lazy val root =
    project
    .in(file("."))
    .settings(
        name := "researcher-night-demo",
        libraryDependencies += ("it.unibo.scafi" %% "scafi-core" % "1.8.0").cross(CrossVersion.for3Use2_13),
        libraryDependencies += ("it.unibo.scafi" %% "scafi-simulator" % "1.6.0").cross(CrossVersion.for3Use2_13),
        libraryDependencies += "com.lihaoyi" %% "requests" % "0.9.3",
        libraryDependencies += "com.lihaoyi" %% "upickle" % "4.4.3",
        libraryDependencies += "ch.qos.logback" % "logback-classic" % "1.6.6",
        libraryDependencies += "org.typelevel" %% "cats-effect" % "3.5.7",
        libraryDependencies += "org.typelevel" %% "log4cats-slf4j" % "2.8.0",
        libraryDependencies += "org.eclipse.paho" % "org.eclipse.paho.client.mqttv3" % "1.2.5",
        // Compiles formations published at runtime. Pinned to scalaVersion: it must read the
        // TASTy this very build produced.
        libraryDependencies += "org.scala-lang" %% "scala3-compiler" % scalaVersion.value,
        libraryDependencies += "org.scalameta" %% "munit" % "1.1.2" % Test,
        // The embedded compiler resolves symbols from `java.class.path`; sbt's in-process test
        // runner would hand it sbt's own launcher instead of the test classpath.
        Test / fork := true,
        assembly / mainClass := Some("it.unibo.demo.ResearchNightDemos"),
        mainClass := Some("it.unibo.demo.ResearchNightDemos")
    )
