# Multi-stage Dockerfile for SBT project

# Stage 1: Build stage
FROM sbtscala/scala-sbt:eclipse-temurin-21.0.7_6_1.11.4_3.7.2 AS builder

WORKDIR /app

COPY project/build.properties project/
COPY project/plugins.sbt project/
COPY project/*.scala project/

# Copy build.sbt
COPY build.sbt .
COPY aggregate-runtime/ aggregate-runtime/
RUN sbt clean assembly

FROM openjdk:21-jdk-slim

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY --from=builder /app/aggregate-runtime/target/scala-*//*-assembly-*.jar app.jar
RUN chown -R appuser:appuser /app
USER appuser

# Run the application
CMD ["java", "-jar", "app.jar"]