# Use official Python image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*


# Copy poetry files and README
COPY poetry.lock pyproject.toml README.md ./

# Install Poetry
RUN pip install --no-cache-dir poetry

# Install dependencies (no project install)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# Copy project files
COPY src/ ./src/


# Set environment variables for parameters (can be overridden at runtime)
ENV PYTHONPATH="/app/src" \
    ROBOTS=100 \
    MQTT="mqtt://localhost:1883" \
    WORLD_SIZE=100.0 \
    NEIGHBORHOOD_RANGE=20.0 \
    SEND_NEIGHBORS=False

# Default command uses python and environment variables
CMD ["sh", "-c", "python src/robot_emulation/main.py --robots $ROBOTS --mqtt $MQTT --world-size $WORLD_SIZE --neighborhood-range $NEIGHBORHOOD_RANGE --send-neighbors $SEND_NEIGHBORS"]
