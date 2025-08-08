ThisBuild / version := "0.1.0-SNAPSHOT"

val underlineJava = System.getProperty("java.version").split("\\.")(0)

ThisBuild / scalaVersion := "3.4.3"
val slf4jVersion = "2.0.16"
lazy val root = 
    project
    .in(file("aggregate-runtime"))
    .settings(
        name := "researcher-night-demo",
        libraryDependencies += ("it.unibo.scafi" %% "scafi-core" % "1.3.0").cross(CrossVersion.for3Use2_13),
        libraryDependencies += ("it.unibo.scafi" %% "scafi-simulator" % "1.3.0").cross(CrossVersion.for3Use2_13),
        libraryDependencies += "org.scalafx" %% "scalafx" % "16.0.0-R24",
        libraryDependencies += "com.lihaoyi" %% "requests" % "0.9.0",
        libraryDependencies += "com.lihaoyi" %% "upickle" % "3.3.0",
        libraryDependencies += "org.slf4j" % "slf4j-simple" % slf4jVersion,
        libraryDependencies += "ch.qos.logback" % "logback-classic" % "1.5.7",
        libraryDependencies += "org.bytedeco" % "javacv-platform" % "1.5.10",
        libraryDependencies += "org.eclipse.paho" % "org.eclipse.paho.client.mqttv3" % "1.2.5",
        libraryDependencies ++= {
        // Determine OS version of JavaFX binaries
        lazy val osName = System.getProperty("os.name") match {
            case n if n.startsWith("Linux") => "linux"
            case n if n.startsWith("Mac") => "mac"
            case n if n.startsWith("Windows") => "win"
            case _ => throw new Exception("Unknown platform!")
        }
        Seq("base", "controls", "fxml", "graphics", "media", "swing", "web")
            .map(m => "org.openjfx" % s"javafx-$m" % underlineJava) 
        }
    )