# Use official Python image as base
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy pyproject.toml
COPY pyproject.toml ./

# Install dependencies (no project install)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --no-directory

# Copy project files
COPY src/ ./src/

# Set environment variables for parameters
ENV PYTHONPATH="/app/src" \
    ROBOTS=12 \
    MQTT_BROKER="mosquitto" \
    MQTT_PORT=1883 \
    WORLD_SIZE=10.0 \
    NEIGHBORHOOD_RANGE=20.0 \
    SEND_NEIGHBORS=False

# Default command uses python and environment variables
CMD ["sh", "-c", "python src/robot_emulator/main.py"]
