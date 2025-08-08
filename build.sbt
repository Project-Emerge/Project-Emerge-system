ThisBuild / version := "0.1.0-SNAPSHOT"
ThisBuild / assemblyMergeStrategy := {
  case PathList("javax", "servlet", xs @ _*)         => MergeStrategy.first
  case PathList(ps @ _*) if ps.last endsWith ".html" => MergeStrategy.first
  case "application.conf"                            => MergeStrategy.concat
  case "unwanted.txt"                                => MergeStrategy.discard
  case "module-info.class"                           => MergeStrategy.discard
  case PathList("META-INF", "versions", "9", "module-info.class") => MergeStrategy.discard
  case PathList("META-INF", xs @ _*) => MergeStrategy.discard
  case x =>
    val oldStrategy = (ThisBuild / assemblyMergeStrategy).value
    oldStrategy(x)
}

val underlineJava = System.getProperty("java.version").split("\\.")(0)

ThisBuild / scalaVersion := "3.7.1"
val slf4jVersion = "2.0.16"
lazy val root = 
    project
    .in(file("aggregate-runtime"))
    .settings(
        name := "researcher-night-demo",
        libraryDependencies += ("it.unibo.scafi" %% "scafi-core" % "1.3.0").cross(CrossVersion.for3Use2_13),
        libraryDependencies += ("it.unibo.scafi" %% "scafi-simulator" % "1.3.0").cross(CrossVersion.for3Use2_13),
        libraryDependencies += "com.lihaoyi" %% "requests" % "0.9.0",
        libraryDependencies += "com.lihaoyi" %% "upickle" % "3.3.0",
        libraryDependencies += "org.slf4j" % "slf4j-simple" % slf4jVersion,
        libraryDependencies += "ch.qos.logback" % "logback-classic" % "1.5.7",
        libraryDependencies += "org.eclipse.paho" % "org.eclipse.paho.client.mqttv3" % "1.2.5",
        assembly / mainClass := Some("it.unibo.demo.HeadlessFormation"),
    )
