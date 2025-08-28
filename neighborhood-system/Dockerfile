# Simple Dockerfile for Neighborhood Server
FROM python:3.13-slim

WORKDIR /app

COPY neighborhood-system/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY neighborhood-system/main.py ./main.py

# Set default MQTT broker URL, can be overridden at runtime
ENV MQTT_BROKER=localhost
ENV MQTT_PORT=1883

EXPOSE 5000

CMD ["python", "main.py"]
